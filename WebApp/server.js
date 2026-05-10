const express = require("express");
const multer = require("multer");
const AdmZip = require("adm-zip");
const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const crypto = require("crypto");

const app = express();
const PORT = Number(process.env.PORT || 8090);
const MAX_UPLOAD_FILES = Number(process.env.MAX_UPLOAD_FILES || 50000);
const MAX_UPLOAD_FILE_SIZE = Number(process.env.MAX_UPLOAD_FILE_SIZE || 1024 * 1024 * 1024);
// When packaged inside an .asar, __dirname is read-only.
// Electron sets WEBAPP_DATA_DIR to app.getPath('userData') before requiring this module.
const DATA_DIR = process.env.WEBAPP_DATA_DIR || __dirname;
const ROOT_DIR = __dirname;
const STORAGE_DIR = path.join(DATA_DIR, "storage", "books");
const LIBRARY_FILE = path.join(DATA_DIR, "storage", "library.json");
const TMP_DIR = path.join(DATA_DIR, "storage", "tmp");

ensureDirSync(STORAGE_DIR);
ensureDirSync(TMP_DIR);
ensureLibraryFileSync();

const upload = multer({
  dest: TMP_DIR,
  limits: {
    files: MAX_UPLOAD_FILES,
    fileSize: MAX_UPLOAD_FILE_SIZE,
  },
});

app.use(express.json({ limit: "2mb" }));
app.use("/media", express.static(STORAGE_DIR));
app.use(express.static(path.join(ROOT_DIR, "public")));

app.get("/api/health", (_req, res) => {
  res.json({ ok: true });
});

app.get("/api/books", async (_req, res) => {
  const library = await readLibrary();
  await repairLibraryMetadataIfNeeded(library);
  res.json({ books: library.books });
});

app.delete("/api/books/:id", async (req, res) => {
  const { id } = req.params;
  const library = await readLibrary();
  const existing = library.books.find((b) => b.id === id);
  if (!existing) {
    return res.status(404).json({ error: "Book not found" });
  }

  const bookDir = path.join(STORAGE_DIR, id);
  await fsp.rm(bookDir, { recursive: true, force: true });

  library.books = library.books.filter((b) => b.id !== id);
  await writeLibrary(library);
  res.json({ ok: true });
});

app.get("/api/books/:id/index", async (req, res) => {
  const { id } = req.params;
  const library = await readLibrary();
  await repairLibraryMetadataIfNeeded(library);
  const book = library.books.find((b) => b.id === id);

  if (!book) {
    return res.status(404).json({ error: "Book not found" });
  }

  res.json({ book });
});

app.post("/api/install", upload.array("files", MAX_UPLOAD_FILES), async (req, res) => {
  const uploaded = req.files || [];
  const paths = normalizePathField(req.body.paths);

  if (!uploaded.length) {
    return res.status(400).json({ error: "No files uploaded" });
  }

  if (paths.length && paths.length !== uploaded.length) {
    await cleanupTemp(uploaded);
    return res.status(400).json({ error: "Paths array must match files count" });
  }

  const importId = makeId();
  const sourceDir = path.join(STORAGE_DIR, importId, "source");
  await fsp.mkdir(sourceDir, { recursive: true });

  const copied = [];
  try {
    for (let i = 0; i < uploaded.length; i += 1) {
      const tmpFile = uploaded[i];
      const relative = normalizeImportedPath(
        paths[i] || tmpFile.originalname || tmpFile.filename
      );
      if (!relative) {
        continue;
      }

      const dest = path.join(sourceDir, relative);
      const parent = path.dirname(dest);
      await fsp.mkdir(parent, { recursive: true });
      await fsp.copyFile(tmpFile.path, dest);
      copied.push(relative);
    }

    const audioFiles = copied.filter((p) => /(^|\/)audio\/.+\.(mp3|m4a|aac|ogg)$/i.test(p));
    const indexPath = copied.find((p) => /(^|\/)sync\/_index\.json$/i.test(p));

    if (!audioFiles.length || !indexPath) {
      await fsp.rm(path.join(STORAGE_DIR, importId), { recursive: true, force: true });
      await cleanupTemp(uploaded);
      return res.status(400).json({
        error: "Missing required files. Expected audio/* files and sync/_index.json.",
      });
    }

    const syncIndexAbs = path.join(sourceDir, indexPath);
    const syncIndex = await safeReadJson(syncIndexAbs);
    const chapters = buildChapters(syncIndex, audioFiles);

    const epubPath = pickEpubPath(copied);
    const epubMetadata = epubPath
      ? await extractEpubMetadata(path.join(sourceDir, epubPath))
      : null;

    const { title, author } = inferTitleAuthor(syncIndex, copied, epubMetadata);
    const coverPath = epubPath;

    let coverUrl = null;
    if (coverPath) {
      const epubAbs = path.join(sourceDir, coverPath);
      const bookDir = path.join(STORAGE_DIR, importId);
      const coverFile = await extractEpubCover(epubAbs, bookDir);
      if (coverFile) {
        coverUrl = `/media/${importId}/${coverFile}`;
      }
    }

    const book = {
      id: importId,
      title,
      author,
      createdAt: new Date().toISOString(),
      chapterCount: chapters.length,
      coverPath,
      coverUrl,
      baseUrl: `/media/${importId}/source`,
      chapters,
    };

    const library = await readLibrary();
    library.books.unshift(book);
    await writeLibrary(library);

    await cleanupTemp(uploaded);
    res.json({ ok: true, book });
  } catch (err) {
    await cleanupTemp(uploaded);
    await fsp.rm(path.join(STORAGE_DIR, importId), { recursive: true, force: true });
    console.error(err);
    res.status(500).json({ error: "Import failed" });
  }
});

app.get("*", (_req, res) => {
  res.sendFile(path.join(ROOT_DIR, "public", "index.html"));
});

app.use((err, _req, res, _next) => {
  if (err instanceof multer.MulterError) {
    if (err.code === "LIMIT_FILE_COUNT") {
      return res.status(400).json({
        error: `Too many files uploaded. Max allowed is ${MAX_UPLOAD_FILES}.`,
      });
    }
    if (err.code === "LIMIT_FILE_SIZE") {
      return res.status(400).json({
        error: `One or more files exceed max file size (${MAX_UPLOAD_FILE_SIZE} bytes).`,
      });
    }
    return res.status(400).json({ error: err.message });
  }

  console.error(err);
  return res.status(500).json({ error: "Unexpected server error" });
});

function createServer(port = PORT) {
  return app.listen(port, () => {
    console.log(`Web app listening on http://localhost:${port}`);
  });
}

if (require.main === module) {
  createServer();
}

module.exports = {
  app,
  createServer,
};

function ensureDirSync(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function ensureLibraryFileSync() {
  if (!fs.existsSync(LIBRARY_FILE)) {
    fs.writeFileSync(LIBRARY_FILE, JSON.stringify({ books: [] }, null, 2), "utf8");
  }
}

async function readLibrary() {
  const raw = await fsp.readFile(LIBRARY_FILE, "utf8");
  const parsed = JSON.parse(raw);
  if (!Array.isArray(parsed.books)) {
    return { books: [] };
  }
  return parsed;
}

async function writeLibrary(library) {
  await fsp.writeFile(LIBRARY_FILE, JSON.stringify(library, null, 2), "utf8");
}

async function repairLibraryMetadataIfNeeded(library) {
  if (!library || !Array.isArray(library.books) || !library.books.length) {
    return;
  }

  let changed = false;
  for (const book of library.books) {
    const titleMissing = !book?.title || /^imported audiobook$/i.test(String(book.title).trim());
    const authorMissing = !book?.author || /^unknown author$/i.test(String(book.author).trim());
    if (!titleMissing && !authorMissing) {
      continue;
    }

    const sourceDir = path.join(STORAGE_DIR, String(book.id || ""), "source");
    const epubAbs = await findFirstEpubFile(sourceDir);
    if (!epubAbs) {
      continue;
    }

    const meta = await extractEpubMetadata(epubAbs);
    const epubRel = path.relative(sourceDir, epubAbs).split(path.sep).join("/");
    const inferredAuthor = normalizeAuthorValue(meta?.author || extractAuthorFromEpubFilename(epubRel));
    const inferredTitle = normalizeAuthorValue(meta?.title);

    if (authorMissing && inferredAuthor) {
      book.author = inferredAuthor;
      changed = true;
    }
    if (titleMissing && inferredTitle) {
      book.title = inferredTitle;
      changed = true;
    }
  }

  if (changed) {
    await writeLibrary(library);
  }
}

async function findFirstEpubFile(rootDir) {
  try {
    const entries = await fsp.readdir(rootDir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.name.startsWith("._")) continue;
      const abs = path.join(rootDir, entry.name);
      if (entry.isFile() && /\.epub$/i.test(entry.name)) {
        return abs;
      }
      if (entry.isDirectory()) {
        const nested = await findFirstEpubFile(abs);
        if (nested) return nested;
      }
    }
  } catch (_err) {
    return null;
  }
  return null;
}

function makeId() {
  return crypto.randomUUID();
}

function normalizePathField(pathField) {
  if (!pathField) {
    return [];
  }
  return Array.isArray(pathField) ? pathField : [pathField];
}

function sanitizeRelativePath(value) {
  if (!value) {
    return "";
  }

  const normalized = String(value)
    .replace(/\\/g, "/")
    .replace(/^\/+/, "")
    .split("/")
    .filter((part) => part && part !== "." && part !== "..");

  if (!normalized.length) {
    return "";
  }

  return normalized.join("/");
}

function normalizeImportedPath(value) {
  const safe = sanitizeRelativePath(value);
  if (!safe) {
    return "";
  }

  const markerMatch = safe.match(/(^|\/)(audio|sync|ebook)\//i);
  if (!markerMatch) {
    return safe;
  }

  const marker = markerMatch[2].toLowerCase() + "/";
  const markerIndex = safe.toLowerCase().indexOf(marker);
  if (markerIndex < 0) {
    return safe;
  }

  return safe.slice(markerIndex);
}

function pickEpubPath(copied) {
  if (!Array.isArray(copied)) return null;

  // Prefer canonical ebook/ folder when present.
  const preferred = copied.find((p) => /(^|\/)ebook\/.+\.epub$/i.test(p) && !isAppleDoublePath(p));
  if (preferred) return preferred;

  // Fall back to any .epub uploaded anywhere in the tree.
  return copied.find((p) => /\.epub$/i.test(p) && !isAppleDoublePath(p)) || null;
}

function isAppleDoublePath(p) {
  return /(^|\/)\._/.test(String(p || ''));
}

async function cleanupTemp(uploaded) {
  await Promise.all(
    uploaded.map((f) => fsp.rm(f.path, { recursive: true, force: true }))
  );
}

async function safeReadJson(filePath) {
  const raw = await fsp.readFile(filePath, "utf8");
  return JSON.parse(raw);
}

function buildChapters(syncIndex, audioFiles) {
  const orderedAudio = [...audioFiles].sort();

  if (Array.isArray(syncIndex?.chapters) && syncIndex.chapters.length) {
    return syncIndex.chapters.map((c, idx) => {
      const audioFile = pickAudioFile(c, orderedAudio, idx);
      const chapterName = c.title || c.name || cleanChapterName(path.basename(audioFile, path.extname(audioFile)), idx);
      return {
        index: idx,
        name: chapterName || `Chapter ${idx + 1}`,
        audioPath: `audio/${audioFile}`,
        syncPath: `sync/${pickSyncFile(c, idx, audioFile)}`,
      };
    });
  }

  return orderedAudio.map((audioRelative, idx) => {
    const baseName = path.basename(audioRelative, path.extname(audioRelative));
    return {
      index: idx,
      name: cleanChapterName(baseName, idx),
      audioPath: audioRelative.startsWith("audio/") ? audioRelative : `audio/${path.basename(audioRelative)}`,
      syncPath: null,
    };
  });
}

function pickAudioFile(chapter, orderedAudio, idx) {
  const candidates = [
    chapter.audio,
    chapter.audio_file,
    chapter.audioFile,
    chapter.file,
    chapter.filename,
  ].filter(Boolean);

  for (const candidate of candidates) {
    const clean = String(candidate).replace(/^audio\//, "");
    if (orderedAudio.some((f) => f.endsWith(clean))) {
      return clean;
    }
  }

  const fallback = orderedAudio[idx] || orderedAudio[0];
  return fallback.replace(/^audio\//, "");
}

function pickSyncFile(chapter, idx, audioFile) {
  const candidate = chapter.sync || chapter.sync_file || chapter.syncFile || chapter.json || chapter.filename;
  if (candidate && String(candidate).toLowerCase().endsWith(".json")) {
    return String(candidate).replace(/^sync\//, "");
  }

  if (audioFile) {
    return `${path.basename(audioFile, path.extname(audioFile))}.json`;
  }

  return `${String(idx + 1).padStart(2, "0")}.json`;
}

function cleanChapterName(raw, idx) {
  return String(raw)
    .replace(/^\d+\s*[-_.]\s*/u, "")
    .replace(/[_-]+/g, " ")
    .trim() || `Chapter ${idx + 1}`;
}

function inferTitleAuthor(syncIndex, copied, epubMetadata = null) {
  const epubPath = pickEpubPath(copied);
  const rawTitle =
    syncIndex?.book?.title ||
    syncIndex?.meta?.title ||
    syncIndex?.title ||
    epubMetadata?.title ||
    "Imported Audiobook";

  const rawAuthor = normalizeAuthorValue(
    syncIndex?.book?.author ||
    syncIndex?.meta?.author ||
    syncIndex?.author ||
    epubMetadata?.author ||
    extractAuthorFromEpubFilename(epubPath) ||
    extractAuthorFromFilenames(copied) ||
    "Unknown Author"
  );

  return {
    title: String(rawTitle).trim() || "Imported Audiobook",
    author: String(rawAuthor).trim() || "Unknown Author",
  };
}

function normalizeAuthorValue(value) {
  if (Array.isArray(value)) {
    for (const item of value) {
      const normalized = normalizeAuthorValue(item);
      if (normalized) {
        return normalized;
      }
    }
    return "";
  }

  if (value && typeof value === "object") {
    const objectCandidate = value.name || value.creator || value.author || value.text;
    return normalizeAuthorValue(objectCandidate);
  }

  return value ? String(value).trim() : "";
}

function extractAuthorFromFilenames(copied) {
  const audio = copied.find((p) => p.startsWith("audio/"));
  if (!audio) {
    return null;
  }

  const name = path.basename(audio, path.extname(audio));
  const parts = name.split(" - ").map((s) => s.trim()).filter(Boolean);

  if (parts.length >= 3) {
    return parts[1];
  }

  return null;
}

function extractAuthorFromEpubFilename(epubPath) {
  if (!epubPath) return null;

  const stem = path.basename(String(epubPath), path.extname(String(epubPath)));
  if (!stem) return null;

  const parts = stem.split(/\s*-\s*|_-/).map((s) => s.trim()).filter(Boolean);
  if (parts.length >= 2) {
    // Typical form: Last_First_Middle_-_Series_-_Title
    const nameBits = parts[0].split(/[_\s]+/).filter(Boolean);
    if (nameBits.length >= 2) {
      const last = nameBits[0];
      const given = nameBits.slice(1).join(" ");
      return `${given} ${last}`.replace(/\s+/g, " ").trim();
    }
  }

  return null;
}

async function extractEpubCover(epubAbsPath, destDir) {
  try {
    const zip = new AdmZip(epubAbsPath);

    // Step 1: Read META-INF/container.xml to find OPF path
    const containerEntry = zip.getEntry("META-INF/container.xml");
    if (!containerEntry) return null;

    const containerXml = containerEntry.getData().toString("utf8");
    const opfMatch = containerXml.match(/full-path="([^"]+\.opf)"/i);
    if (!opfMatch) return null;

    const opfPath = opfMatch[1];
    const opfDir = opfPath.includes("/") ? opfPath.substring(0, opfPath.lastIndexOf("/") + 1) : "";

    const opfEntry = zip.getEntry(opfPath);
    if (!opfEntry) return null;

    const opfXml = opfEntry.getData().toString("utf8");

    // Step 2: Locate cover image href from OPF
    let coverHref = null;

    // EPUB3: <item properties="cover-image" href="..."/>
    const ep3 = opfXml.match(/<item[^>]+properties="cover-image"[^>]+href="([^"]+)"/i)
      || opfXml.match(/<item[^>]+href="([^"]+)"[^>]+properties="cover-image"/i);
    if (ep3) coverHref = ep3[1];

    // EPUB2: <meta name="cover" content="id"/> then look up item by id
    if (!coverHref) {
      const metaMatch = opfXml.match(/<meta[^>]+name="cover"[^>]+content="([^"]+)"/i)
        || opfXml.match(/<meta[^>]+content="([^"]+)"[^>]+name="cover"/i);
      if (metaMatch) {
        const coverId = metaMatch[1].replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        const itemMatch = opfXml.match(new RegExp(`<item[^>]+id="${coverId}"[^>]+href="([^"]+)"`, "i"))
          || opfXml.match(new RegExp(`<item[^>]+href="([^"]+)"[^>]+id="${coverId}"`, "i"));
        if (itemMatch) coverHref = itemMatch[1];
      }
    }

    if (!coverHref) return null;

    // Step 3: Extract image from ZIP
    const coverPathInZip = opfDir + coverHref;
    const coverEntry = zip.getEntry(coverPathInZip);
    if (!coverEntry) return null;

    const ext = path.extname(coverHref).toLowerCase() || ".jpg";
    const allowed = [".jpg", ".jpeg", ".png", ".webp", ".gif"];
    if (!allowed.includes(ext)) return null;

    const destFile = path.join(destDir, `cover${ext}`);
    await fsp.writeFile(destFile, coverEntry.getData());

    return `cover${ext}`;
  } catch (err) {
    console.warn("Could not extract EPUB cover:", err.message);
    return null;
  }
}

async function extractEpubMetadata(epubAbsPath) {
  try {
    const zip = new AdmZip(epubAbsPath);
    const opf = getOpfInfo(zip);
    if (!opf?.opfXml) {
      return null;
    }

    const opfXml = opf.opfXml;
    const title = firstMatch(opfXml, [
      /<dc:title[^>]*>([\s\S]*?)<\/dc:title>/i,
      /<title[^>]*>([\s\S]*?)<\/title>/i,
    ]);

    const creatorMatches = [...opfXml.matchAll(/<dc:creator[^>]*>([\s\S]*?)<\/dc:creator>/gi)]
      .map((m) => decodeXmlEntities(m[1]).trim())
      .filter(Boolean);
    const author = creatorMatches.join(", ") || null;

    return {
      title: title ? decodeXmlEntities(title).trim() : null,
      author,
    };
  } catch (_err) {
    return null;
  }
}

function getOpfInfo(zip) {
  const containerEntry = zip.getEntry("META-INF/container.xml");
  if (!containerEntry) return null;

  const containerXml = containerEntry.getData().toString("utf8");
  const opfMatch = containerXml.match(/full-path="([^"]+\.opf)"/i);
  if (!opfMatch) return null;

  const opfPath = opfMatch[1];
  const opfDir = opfPath.includes("/") ? opfPath.substring(0, opfPath.lastIndexOf("/") + 1) : "";
  const opfEntry = zip.getEntry(opfPath);
  if (!opfEntry) return null;

  return {
    opfPath,
    opfDir,
    opfXml: opfEntry.getData().toString("utf8"),
  };
}

function firstMatch(value, patterns) {
  for (const pattern of patterns) {
    const match = value.match(pattern);
    if (match && match[1]) {
      return match[1];
    }
  }
  return "";
}

function decodeXmlEntities(value) {
  return String(value)
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&#39;/g, "'")
    .replace(/&#x27;/gi, "'")
    .replace(/&#x2F;/gi, "/");
}
