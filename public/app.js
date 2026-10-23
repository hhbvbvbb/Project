const loginScreen = document.querySelector("#login-screen");
const chatScreen = document.querySelector("#chat-screen");
const loginForm = document.querySelector("#login-form");
const loginError = document.querySelector("#login-error");
const conversationList = document.querySelector("#conversation-list");
const messageList = document.querySelector("#message-list");
const welcomePanel = document.querySelector("#welcome-panel");
const composer = document.querySelector("#composer");
const messageInput = document.querySelector("#message-input");
const fileInput = document.querySelector("#file-input");
const filePreview = document.querySelector("#file-preview");
const groupDialog = document.querySelector("#group-dialog");
const groupForm = document.querySelector("#group-form");
const groupError = document.querySelector("#group-error");

let currentUser = null;
let users = [];
let conversations = [];
let activeConversation = null;
let latestMessageId = 0;
let selectedFile = null;
let refreshBusy = false;

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  const contentType = response.headers.get("content-type") || "";
  const result = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    if (response.status === 401 && path !== "/api/login") showLogin();
    throw new Error(result?.error || `Request failed (${response.status})`);
  }
  return result;
}

function showLogin() {
  currentUser = null;
  loginScreen.hidden = false;
  chatScreen.hidden = true;
  loginForm.reset();
}

function showChat(user, roster) {
  currentUser = user;
  users = roster;
  loginScreen.hidden = true;
  chatScreen.hidden = false;
  document.querySelector("#user-name").textContent = user.displayName;
  document.querySelector("#user-avatar").textContent = initials(user.displayName);
  document.querySelector("#user-role").hidden = user.role !== "owner";
  renderMemberOptions();
  loadConversations().catch(showError);
}

function initials(name) {
  return name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
}

function showError(error) {
  console.error(error);
  const message = error instanceof Error ? error.message : "Something went wrong";
  if (!chatScreen.hidden) {
    let notice = document.querySelector("#chat-notice");
    if (!notice) {
      notice = document.createElement("p");
      notice.id = "chat-notice";
      notice.className = "form-error";
      notice.setAttribute("role", "alert");
      document.querySelector(".chat-header").after(notice);
    }
    notice.textContent = message;
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginError.hidden = true;
  const form = new FormData(loginForm);
  try {
    const result = await api("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: form.get("username"),
        password: form.get("password"),
      }),
    });
    showChat(result.user, result.users);
  } catch (error) {
    loginError.textContent = error.message;
    loginError.hidden = false;
  }
});

document.querySelector("#logout-button").addEventListener("click", async () => {
  try {
    await api("/api/logout", { method: "POST" });
    activeConversation = null;
    showLogin();
  } catch (error) {
    showError(error);
  }
});

document.querySelector("#mobile-menu").addEventListener("click", () => {
  chatScreen.classList.toggle("menu-open");
});

async function loadConversations() {
  const result = await api("/api/conversations");
  conversations = result.conversations;
  renderConversations();
  if (!activeConversation && conversations.length) selectConversation(conversations[0].id);
  else if (activeConversation) renderActiveConversation();
}

function renderConversations() {
  conversationList.replaceChildren();
  for (const conversation of conversations) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `conversation${conversation.id === activeConversation ? " active" : ""}`;
    button.setAttribute("aria-current", conversation.id === activeConversation ? "page" : "false");

    const avatar = document.createElement("span");
    avatar.className = "channel-avatar";
    avatar.textContent = conversation.kind === "group" ? "#" : initials(conversation.name);
    const copy = document.createElement("span");
    copy.className = "conversation-copy";
    const title = document.createElement("strong");
    title.textContent = conversation.name;
    const preview = document.createElement("span");
    preview.textContent = conversation.lastMessage || (conversation.kind === "group" ? "Private group" : "Start a conversation");
    copy.append(title, preview);
    button.append(avatar, copy);
    if (conversation.kind === "group") {
      const tag = document.createElement("span");
      tag.className = "group-tag";
      tag.textContent = "GROUP";
      button.append(tag);
    }
    button.addEventListener("click", () => selectConversation(conversation.id));
    conversationList.append(button);
  }
}

function renderActiveConversation() {
  const conversation = conversations.find((entry) => entry.id === activeConversation);
  if (!conversation) return;
  document.querySelector("#channel-name").textContent = conversation.name;
  document.querySelector("#channel-meta").textContent =
    conversation.kind === "group" ? "PRIVATE GROUP CHANNEL" : "PRIVATE DIRECT CHANNEL";
  document.querySelector("#channel-icon").textContent = conversation.kind === "group" ? "#" : "↗";
  document.querySelector("#member-count").textContent = String(conversation.members.length);
}

async function selectConversation(id) {
  activeConversation = id;
  latestMessageId = 0;
  renderConversations();
  renderActiveConversation();
  welcomePanel.hidden = true;
  messageList.hidden = false;
  composer.hidden = false;
  messageList.replaceChildren();
  chatScreen.classList.remove("menu-open");
  await loadMessages(true);
  messageInput.focus();
}

async function loadMessages(initial = false) {
  if (!activeConversation) return;
  const params = new URLSearchParams({ conversation: activeConversation });
  if (!initial) params.set("after", String(latestMessageId));
  const result = await api(`/api/messages?${params}`);
  if (initial) messageList.replaceChildren();
  for (const message of result.messages) {
    if (message.id <= latestMessageId) continue;
    appendMessage(message);
    latestMessageId = message.id;
  }
  if (result.messages.length) messageList.scrollTop = messageList.scrollHeight;
}

function appendMessage(message) {
  const article = document.createElement("article");
  article.className = "message";
  const avatar = document.createElement("span");
  avatar.className = "avatar";
  avatar.textContent = initials(message.author);
  const content = document.createElement("div");
  content.className = "message-content";
  const top = document.createElement("div");
  top.className = "message-top";
  const name = document.createElement("strong");
  name.textContent = message.author;
  const time = document.createElement("time");
  time.dateTime = new Date(message.createdAt).toISOString();
  time.textContent = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(message.createdAt);
  top.append(name, time);
  content.append(top);
  if (message.text) {
    const text = document.createElement("p");
    text.className = "message-text";
    text.textContent = message.text;
    content.append(text);
  }
  if (message.file) {
    const link = document.createElement("a");
    link.className = "file-card";
    link.href = `/api/files/${message.file.id}`;
    const symbol = document.createElement("span");
    symbol.className = "file-symbol";
    symbol.textContent = "↓";
    const detail = document.createElement("span");
    detail.className = "file-detail";
    const fileName = document.createElement("span");
    fileName.textContent = message.file.name;
    const size = document.createElement("span");
    size.textContent = formatBytes(message.file.size);
    detail.append(fileName, size);
    link.append(symbol, detail);
    content.append(link);
  }
  article.append(avatar, content);
  messageList.append(article);
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!activeConversation) return;
  const text = messageInput.value.trim();
  if (!text && !selectedFile) return;
  const button = composer.querySelector(".send-button");
  button.disabled = true;
  try {
    if (selectedFile) {
      const form = new FormData();
      form.set("conversationId", activeConversation);
      form.set("text", text);
      form.set("file", selectedFile);
      await api("/api/files", { method: "POST", body: form });
      clearSelectedFile();
    } else {
      await api("/api/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversationId: activeConversation, text }),
      });
    }
    messageInput.value = "";
    resizeComposer();
    await loadMessages();
    await loadConversations();
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
  }
});

messageInput.addEventListener("input", resizeComposer);
messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    composer.requestSubmit();
  }
});

function resizeComposer() {
  messageInput.style.height = "auto";
  messageInput.style.height = `${Math.min(messageInput.scrollHeight, 110)}px`;
}

document.querySelector("#attach-button").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  selectedFile = fileInput.files?.[0] || null;
  if (!selectedFile) {
    filePreview.hidden = true;
    return;
  }
  filePreview.textContent = `${selectedFile.name} · ${formatBytes(selectedFile.size)} `;
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "icon-button";
  remove.textContent = "×";
  remove.setAttribute("aria-label", "Remove attachment");
  remove.addEventListener("click", clearSelectedFile);
  filePreview.append(remove);
  filePreview.hidden = false;
});

function clearSelectedFile() {
  selectedFile = null;
  fileInput.value = "";
  filePreview.replaceChildren();
  filePreview.hidden = true;
}

document.querySelector("#new-group-button").addEventListener("click", () => {
  groupError.hidden = true;
  groupForm.reset();
  groupDialog.showModal();
});
document.querySelector("#close-dialog").addEventListener("click", () => groupDialog.close());
document.querySelector("#cancel-group").addEventListener("click", () => groupDialog.close());

function renderMemberOptions() {
  const container = document.querySelector("#group-members");
  container.replaceChildren();
  for (const user of users.filter((entry) => entry.id !== currentUser.id)) {
    const label = document.createElement("label");
    label.className = "member-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.name = "members";
    input.value = user.id;
    const name = document.createElement("span");
    name.textContent = user.displayName;
    label.append(input, name);
    container.append(label);
  }
}

groupForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  groupError.hidden = true;
  const members = [...groupForm.querySelectorAll('input[name="members"]:checked')].map((input) => input.value);
  try {
    const result = await api("/api/groups", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: groupForm.elements.name.value, members }),
    });
    groupDialog.close();
    await loadConversations();
    await selectConversation(result.id);
  } catch (error) {
    groupError.textContent = error.message;
    groupError.hidden = false;
  }
});

async function refresh() {
  if (!currentUser || refreshBusy) return;
  refreshBusy = true;
  try {
    if (activeConversation) await loadMessages();
    await loadConversations();
    const notice = document.querySelector("#chat-notice");
    if (notice) notice.remove();
  } catch (error) {
    showError(error);
  } finally {
    refreshBusy = false;
  }
}

setInterval(refresh, 3000);
api("/api/me")
  .then((result) => showChat(result.user, result.users))
  .catch((error) => {
    if (error.message !== "Sign in required") console.error(error);
    showLogin();
  });
