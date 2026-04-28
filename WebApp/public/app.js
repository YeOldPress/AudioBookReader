const folderInput = document.getElementById("folderInput");
const fileInput = document.getElementById("fileInput");
const installBtn = document.getElementById("installBtn");
const installStatus = document.getElementById("installStatus");
const booksGrid = document.getElementById("booksGrid");
const refreshBtn = document.getElementById("refreshBtn");
const playerTitle = document.getElementById("playerTitle");
const playerMeta = document.getElementById("playerMeta");
const chapterList = document.getElementById("chapterList");
const audio = document.getElementById("audio");

let books = [];
let selectedBook = null;

init();

function init() {
  installBtn.addEventListener("click", onInstallClicked);
  refreshBtn.addEventListener("click", loadBooks);
  loadBooks();
}

async function onInstallClicked() {
  const folderFiles = Array.from(folderInput.files || []);
  const manualFiles = Array.from(fileInput.files || []);
  const files = folderFiles.length ? folderFiles : manualFiles;

  if (!files.length) {
    setStatus("Choose a folder or files first.", true);
    return;
  }

  setStatus(`Uploading ${files.length} files...`);
  installBtn.disabled = true;

  try {
    const form = new FormData();

    for (const file of files) {
      const rel = file.webkitRelativePath || file.name;
      form.append("files", file, file.name);
      form.append("paths", rel);
    }

    const res = await fetch("/api/install", {
      method: "POST",
      body: form,
    });

    const payload = await res.json();
    if (!res.ok) {
      throw new Error(payload.error || "Install failed");
    }

    setStatus(`Installed: ${payload.book.title} by ${payload.book.author}`);
    folderInput.value = "";
    fileInput.value = "";
    await loadBooks(payload.book.id);
  } catch (err) {
    setStatus(err.message || "Install failed", true);
  } finally {
    installBtn.disabled = false;
  }
}

async function loadBooks(openBookId = null) {
  const res = await fetch("/api/books");
  const payload = await res.json();
  books = payload.books || [];
  renderBooks();

  if (!books.length) {
    playerTitle.textContent = "No books installed yet";
    playerMeta.textContent = "Install a book from a mounted disc folder or selected files.";
    chapterList.innerHTML = "";
    audio.removeAttribute("src");
    return;
  }

  if (openBookId) {
    const target = books.find((b) => b.id === openBookId);
    if (target) {
      openBook(target.id);
      return;
    }
  }

  if (!selectedBook || !books.some((b) => b.id === selectedBook.id)) {
    openBook(books[0].id);
  }
}

function renderBooks() {
  booksGrid.innerHTML = "";

  for (const book of books) {
    const card = document.createElement("article");
    card.className = "book-card";

    const title = document.createElement("h3");
    title.textContent = book.title;

    const meta = document.createElement("p");
    meta.textContent = `${book.author} • ${book.chapterCount || 0} chapters`;

    const actions = document.createElement("div");
    actions.className = "book-actions";

    const openBtn = document.createElement("button");
    openBtn.className = "open";
    openBtn.textContent = "Open";
    openBtn.addEventListener("click", () => openBook(book.id));

    const removeBtn = document.createElement("button");
    removeBtn.textContent = "Remove";
    removeBtn.addEventListener("click", () => removeBook(book.id));

    actions.append(openBtn, removeBtn);
    card.append(title, meta, actions);
    booksGrid.appendChild(card);
  }
}

function openBook(bookId) {
  const book = books.find((b) => b.id === bookId);
  if (!book) {
    return;
  }

  selectedBook = book;
  playerTitle.textContent = book.title;
  playerMeta.textContent = `by ${book.author}`;

  renderChapters(book);
}

function renderChapters(book) {
  chapterList.innerHTML = "";
  const chapters = Array.isArray(book.chapters) ? book.chapters : [];

  for (const chapter of chapters) {
    const button = document.createElement("button");
    button.className = "chapter-item";
    button.textContent = chapter.name || `Chapter ${chapter.index + 1}`;

    button.addEventListener("click", () => {
      const src = `${book.baseUrl}/${chapter.audioPath}`;
      audio.src = src;
      audio.play().catch(() => {
        setStatus("Tap play to start audio.");
      });
    });

    chapterList.appendChild(button);
  }

  if (chapters.length) {
    const first = chapters[0];
    audio.src = `${book.baseUrl}/${first.audioPath}`;
  }
}

async function removeBook(bookId) {
  const ok = window.confirm("Remove this installed book from the server?");
  if (!ok) {
    return;
  }

  const res = await fetch(`/api/books/${encodeURIComponent(bookId)}`, {
    method: "DELETE",
  });

  if (!res.ok) {
    setStatus("Could not remove book.", true);
    return;
  }

  if (selectedBook && selectedBook.id === bookId) {
    selectedBook = null;
  }

  await loadBooks();
}

function setStatus(message, isError = false) {
  installStatus.textContent = message;
  installStatus.style.color = isError ? "#b53a2f" : "";
}
