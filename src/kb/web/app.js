"use strict";

// Клиентское приложение без сборки. Токен доступа живёт только в памяти страницы,
// токен обновления лежит в cookie, недоступной скрипту.

const API = "/api/v1";
const page = document.getElementById("page");
const header = document.getElementById("header");

const state = { token: null, user: null };

const METHODS = [
  ["bm25", "BM25"],
  ["tfidf", "TF-IDF"],
  ["hybrid", "Гибридный"],
  ["pgfts", "PostgreSQL FTS"],
];
const SORTS = [
  ["relevance", "По релевантности"],
  ["created", "Сначала новые"],
  ["title", "По названию"],
];
const FIELD_WEIGHTS = [
  ["w_title", "Заголовок документа", 3],
  ["w_headings", "Подзаголовки", 2],
  ["w_tags", "Теги", 2],
  ["w_body", "Текст", 1],
  ["w_code", "Код", 0.5],
];
const ACCESS_NAMES = { owner: "владелец", write: "правка", read: "чтение" };

// Элементы создаются только так: текст из данных попадает в страницу как текст, а не как разметка.
function h(tag, attrs = {}, ...children) {
  const element = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (name.startsWith("on")) element.addEventListener(name.slice(2), value);
    else if (value === true) element.setAttribute(name, "");
    else if (value !== false && value !== null && value !== undefined) {
      element.setAttribute(name, value);
    }
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined && child !== false) element.append(child);
  }
  return element;
}

// Разметку присылает сервер: текст документа в ней уже экранирован.
function serverHtml(tag, className, html) {
  const element = h(tag, { class: className });
  element.innerHTML = html;
  return element;
}

class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function request(method, path, { body, form, params } = {}) {
  const url = new URL(API + path, location.origin);
  for (const [name, value] of Object.entries(params || {})) {
    for (const item of Array.isArray(value) ? value : [value]) {
      if (item !== "" && item !== null && item !== undefined) url.searchParams.append(name, item);
    }
  }
  const headers = {};
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  return fetch(url, {
    method,
    headers,
    credentials: "same-origin",
    body: form || (body !== undefined ? JSON.stringify(body) : undefined),
  });
}

// Код 401 бывает и при неверном пароле в форме: сеанс завершён, только если не подошёл сам токен.
async function tokenRejected(response) {
  if (response.status !== 401 || !state.token) return false;
  const data = await response.clone().json();
  return data?.error?.code === "invalid_token";
}

async function api(method, path, options = {}) {
  let response = await request(method, path, options);
  if ((await tokenRejected(response)) && (await refresh())) {
    response = await request(method, path, options);
  }
  if (await tokenRejected(response)) {
    state.token = null;
    state.user = null;
    location.hash = "#/login";
  }
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const error = data?.error || {};
    throw new ApiError(response.status, error.code, error.message || "Ошибка запроса", error.details);
  }
  return { data, response };
}

async function refresh() {
  const response = await fetch(`${API}/auth/refresh`, { method: "POST", credentials: "same-origin" });
  if (!response.ok) return false;
  state.token = (await response.json()).access_token;
  return true;
}

function errorText(error) {
  if (error.code === "invalid_request" && error.details?.length) {
    return error.details.map((item) => `${item.field}: ${item.message}`).join("; ");
  }
  return error.message || "Не удалось выполнить запрос";
}

// Обёртка для действий пользователя: ошибка показывается рядом с формой.
function guarded(box, action) {
  return async (event) => {
    event?.preventDefault?.();
    box.textContent = "";
    box.className = "message";
    try {
      await action(event);
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      box.className = "message error";
      box.textContent = errorText(error);
    }
  };
}

function say(box, text) {
  box.className = "message ok";
  box.textContent = text;
}

function field(label, attrs) {
  return h("label", {}, label, h("input", attrs));
}

function select(attrs, options, value) {
  return h(
    "select",
    attrs,
    options.map(([key, name]) => h("option", { value: key, selected: key === value }, name)),
  );
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }) : "—";
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
}

function show(narrow, ...children) {
  header.hidden = !state.token;
  page.className = narrow ? "page narrow" : "page";
  page.replaceChildren(...children.flat());
}

function hashParams() {
  const query = location.hash.split("?")[1] || "";
  return new URLSearchParams(query);
}

function tagBadges(tags) {
  return (tags || []).map((tag) => h("span", { class: "badge tag" }, tag));
}

function pager(total, limit, offset, go) {
  if (total <= limit) return null;
  const pageNumber = Math.floor(offset / limit) + 1;
  return h(
    "div",
    { class: "row", id: "pager" },
    h("button", {
      class: "button secondary small", type: "button", disabled: offset === 0,
      onclick: () => go(Math.max(0, offset - limit)),
    }, "Назад"),
    h("span", { class: "muted" }, `Страница ${pageNumber} из ${Math.ceil(total / limit)}`),
    h("button", {
      class: "button secondary small", type: "button", disabled: offset + limit >= total,
      onclick: () => go(offset + limit),
    }, "Вперёд"),
  );
}

// --- Вход и восстановление

function loginPage() {
  const box = h("div", { class: "message", id: "message" });
  const email = field("Адрес электронной почты", { type: "email", name: "email", required: true, autocomplete: "username" });
  const password = field("Пароль", { type: "password", name: "password", required: true, autocomplete: "current-password" });
  show(true, h("h1", {}, "Вход"), h("form", {
    class: "card",
    onsubmit: guarded(box, async () => {
      const { data } = await api("POST", "/auth/login", {
        body: { email: email.querySelector("input").value, password: password.querySelector("input").value },
      });
      state.token = data.access_token;
      await loadUser();
      location.hash = "#/search";
    }),
  }, box, email, password, h("button", { class: "button", type: "submit" }, "Войти")),
  h("p", { class: "muted" }, h("a", { href: "#/register" }, "Зарегистрироваться"), " · ", h("a", { href: "#/forgot" }, "Забыли пароль?")));
}

function registerPage() {
  const box = h("div", { class: "message", id: "message" });
  const email = h("input", { type: "email", name: "email", required: true, autocomplete: "username" });
  const password = h("input", { type: "password", name: "password", required: true, autocomplete: "new-password" });
  const consent = h("input", { type: "checkbox", name: "consent" });
  show(true, h("h1", {}, "Регистрация"), h("form", {
    class: "card",
    onsubmit: guarded(box, async () => {
      const { data } = await api("POST", "/auth/register", {
        body: { email: email.value, password: password.value, consent: consent.checked },
      });
      say(box, `${data.message}. Откройте ссылку из письма, затем войдите.`);
    }),
  }, box,
  h("label", {}, "Адрес электронной почты", email),
  h("label", {}, "Пароль", password, h("span", { class: "muted" }, "Не короче 10 символов, не должен содержать адрес почты")),
  h("label", { class: "check" }, consent, h("span", {}, "Согласен на обработку адреса электронной почты для входа и отправки служебных писем. Учётную запись и все данные можно удалить в любой момент.")),
  h("button", { class: "button", type: "submit" }, "Зарегистрироваться")),
  h("p", { class: "muted" }, h("a", { href: "#/login" }, "Уже есть учётная запись")));
}

async function verifyPage() {
  const box = h("div", { class: "message", id: "message" });
  show(true, h("h1", {}, "Подтверждение адреса"), h("div", { class: "card" }, box, h("a", { href: "#/login" }, "Перейти ко входу")));
  await guarded(box, async () => {
    await api("POST", "/auth/verify-email", { body: { token: hashParams().get("token") || "" } });
    say(box, "Адрес подтверждён. Теперь можно войти.");
  })();
}

function forgotPage() {
  const box = h("div", { class: "message", id: "message" });
  const email = h("input", { type: "email", name: "email", required: true, autocomplete: "username" });
  show(true, h("h1", {}, "Восстановление пароля"), h("form", {
    class: "card",
    onsubmit: guarded(box, async () => {
      const { data } = await api("POST", "/auth/password/forgot", { body: { email: email.value } });
      say(box, `${data.message}. Ссылка действует ограниченное время и работает один раз.`);
    }),
  }, box, h("label", {}, "Адрес электронной почты", email), h("button", { class: "button", type: "submit" }, "Отправить ссылку")),
  h("p", { class: "muted" }, h("a", { href: "#/login" }, "Вернуться ко входу")));
}

function resetPage() {
  const box = h("div", { class: "message", id: "message" });
  const password = h("input", { type: "password", name: "new-password", required: true, autocomplete: "new-password" });
  show(true, h("h1", {}, "Новый пароль"), h("form", {
    class: "card",
    onsubmit: guarded(box, async () => {
      await api("POST", "/auth/password/reset", {
        body: { token: hashParams().get("token") || "", new_password: password.value },
      });
      say(box, "Пароль изменён, все прежние сеансы завершены. Теперь можно войти.");
    }),
  }, box, h("label", {}, "Новый пароль", password), h("button", { class: "button", type: "submit" }, "Сохранить")),
  h("p", { class: "muted" }, h("a", { href: "#/login" }, "Перейти ко входу")));
}

// --- Поиск

function sliderField(name, label, initial, max = 10) {
  const value = h("span", { class: "slider-value" }, String(initial));
  const input = h("input", {
    type: "range", name, min: 0, max, step: max === 1 ? 0.05 : 0.5, value: initial,
    oninput: () => { value.textContent = input.value; input.dataset.changed = "1"; },
  });
  return h("label", {}, label, value, input);
}

function searchPage() {
  const box = h("div", { class: "message", id: "message" });
  const results = h("div", { id: "results" });
  const q = h("input", { type: "search", name: "q", placeholder: "Что найти" });
  const scorer = select({ name: "scorer" }, METHODS, "bm25");
  const sort = select({ name: "sort" }, SORTS, "relevance");
  const tag = h("input", { name: "tag", placeholder: "например, postgresql" });
  const category = h("input", { name: "category" });
  const author = h("input", { name: "author" });
  const from = h("input", { type: "date", name: "created_from" });
  const to = h("input", { type: "date", name: "created_to" });
  const sliders = FIELD_WEIGHTS.map(([name, label, initial]) => sliderField(name, label, initial));
  const hybrid = [sliderField("hybrid_bm25", "Вклад BM25", 0.7, 1), sliderField("hybrid_tfidf", "Вклад TF-IDF", 0.3, 1)];
  const hybridBox = h("div", { class: "grid", hidden: true }, hybrid);
  scorer.addEventListener("change", () => { hybridBox.hidden = scorer.value !== "hybrid"; });

  const run = guarded(box, async () => {
    await runSearch(0);
  });

  async function runSearch(offset) {
    const params = {
      q: q.value, scorer: scorer.value, sort: sort.value, limit: 10, offset,
      tags: tag.value ? tag.value.split(",").map((item) => item.trim()) : [],
      category: category.value, author: author.value,
      created_from: from.value, created_to: to.value,
    };
    for (const element of page.querySelectorAll("input[type=range]")) {
      if (element.dataset.changed) params[element.name] = element.value;
    }
    const { data, response } = await api("GET", "/search", { params });
    const cached = response.headers.get("X-Cache") === "HIT";
    results.replaceChildren(h("div", { class: "card" },
      h("div", { class: "row spread" },
        h("strong", { id: "total" }, `Найдено: ${data.total}`),
        h("span", { class: "muted" }, `Метод: ${data.scorer}`, cached ? " · из кэша" : "")),
      data.hits.length ? data.hits.map(hitRow) : h("p", { class: "muted" }, "Ничего не найдено"),
      pager(data.total, data.limit, data.offset, (next) => guarded(box, () => runSearch(next))()),
    ));
  }

  show(false, h("h1", {}, "Поиск"), h("form", { class: "card", onsubmit: run }, box,
    h("div", { class: "search-line" },
      h("label", {}, "Запрос", q),
      h("label", {}, "Метод", scorer),
      h("label", {}, "Сортировка", sort),
      h("button", { class: "button", type: "submit" }, "Найти")),
    h("details", { class: "sliders" }, h("summary", {}, "Фильтры"),
      h("div", { class: "grid sliders" },
        h("label", {}, "Теги через запятую", tag), h("label", {}, "Категория", category),
        h("label", {}, "Автор", author), h("label", {}, "Создан с", from), h("label", {}, "Создан по", to))),
    h("details", { class: "sliders", id: "weights" }, h("summary", {}, "Веса"),
      h("div", { class: "grid sliders" }, sliders), hybridBox)),
  results);
  run();
}

function hitRow(hit) {
  return h("div", { class: "hit" },
    h("a", { class: "hit-title", href: `#/doc/${hit.id}` }, hit.title),
    serverHtml("div", "hit-snippet", hit.snippet),
    h("div", { class: "row" }, tagBadges(hit.tags),
      hit.category && h("span", { class: "badge" }, hit.category),
      hit.created && h("span", { class: "muted" }, hit.created),
      h("span", { class: "muted" }, `оценка ${hit.score}`)));
}

// --- Документы

async function documentsPage() {
  const box = h("div", { class: "message", id: "message" });
  const list = h("div", { id: "documents" });

  async function load(offset) {
    const { data } = await api("GET", "/documents", { params: { limit: 20, offset } });
    list.replaceChildren(h("div", { class: "card" },
      data.items.length ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ["Название", "Теги", "Версия", "Доступ", "Изменён"].map((name) => h("th", {}, name)))),
        h("tbody", {}, data.items.map((item) => h("tr", {},
          h("td", {}, h("a", { href: `#/doc/${item.id}` }, item.title)),
          h("td", {}, h("div", { class: "row" }, tagBadges(item.tags))),
          h("td", {}, String(item.version)),
          h("td", {}, ACCESS_NAMES[item.access]),
          h("td", { class: "muted" }, formatDate(item.updated_at))))))) : h("p", { class: "muted" }, "Документов пока нет"),
      pager(data.total, data.limit, data.offset, (next) => guarded(box, () => load(next))()),
    ));
  }

  async function upload(files) {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    const { data } = await api("POST", "/documents/import", { form });
    const failed = data.failed.map((item) => `${item.name}: ${item.message}`);
    box.className = failed.length ? "message error" : "message ok";
    box.textContent = `Создано: ${data.created.length}, обновлено: ${data.updated.length}, без изменений: ${data.unchanged.length}, отклонено: ${data.failed.length}.`
      + (failed.length ? ` ${failed.join("; ")}` : "");
    await load(0);
  }

  const input = h("input", { type: "file", id: "file-input", multiple: true, accept: ".md,.markdown" });
  const drop = h("div", { class: "drop", id: "drop" }, "Перетащите файлы Markdown сюда или ",
    h("label", { for: "file-input" }, "выберите на диске"), input);
  input.addEventListener("change", () => guarded(box, () => upload(input.files))());
  drop.addEventListener("dragover", (event) => { event.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    drop.classList.remove("over");
    guarded(box, () => upload(event.dataTransfer.files))();
  });

  show(false, h("div", { class: "row spread" }, h("h1", {}, "Документы"),
    h("a", { class: "button", href: "#/new" }, "Новый документ")),
  h("div", { class: "card" }, box, drop), list);
  await guarded(box, () => load(0))();
}

const NEW_TEMPLATE = "---\ntitle: Новая заметка\ntags: []\n---\n# Новая заметка\n\nТекст заметки.\n";

function newDocumentPage() {
  const box = h("div", { class: "message", id: "message" });
  const editor = h("textarea", { id: "editor", name: "content" }, NEW_TEMPLATE);
  show(false, h("h1", {}, "Новый документ"), h("form", {
    class: "card",
    onsubmit: guarded(box, async () => {
      const { data } = await api("POST", "/documents", { body: { content: editor.value } });
      location.hash = `#/doc/${data.id}`;
    }),
  }, box, editor, h("div", { class: "row" }, h("button", { class: "button", type: "submit" }, "Сохранить"))));
}

async function documentPage(docId) {
  const box = h("div", { class: "message", id: "message" });
  const content = h("div", { id: "tab-content" });
  const url = `/documents/${docId}`;
  let doc;
  try {
    doc = (await api("GET", url)).data;
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    show(false, h("h1", {}, "Документ"), h("div", { class: "card" }, h("div", { class: "message error" }, errorText(error)), h("a", { href: "#/documents" }, "К списку документов")));
    return;
  }

  const canWrite = doc.access !== "read";
  const isOwner = doc.access === "owner";
  const title = h("h1", { id: "doc-title" }, doc.title);

  const tabs = [
    ["view", "Просмотр", showView],
    canWrite && ["edit", "Правка", showEdit],
    ["versions", "Версии", showVersions],
    isOwner && ["shares", "Доступ", showShares],
  ].filter(Boolean);
  const buttons = tabs.map(([key, name, action]) => h("button", {
    type: "button", "data-tab": key,
    onclick: guarded(box, async () => {
      for (const item of buttons) item.classList.toggle("active", item.dataset.tab === key);
      await action();
    }),
  }, name));

  async function showView() {
    const { data } = await api("GET", `${url}/preview`);
    content.replaceChildren(serverHtml("div", "card preview", data.html));
  }

  function showEdit() {
    const editor = h("textarea", { id: "editor", name: "content" }, doc.content);
    content.replaceChildren(h("form", {
      class: "card",
      onsubmit: guarded(box, async () => {
        const { data } = await api("PUT", url, {
          body: { content: editor.value, expected_version: doc.version },
        });
        doc = data;
        title.textContent = doc.title;
        versionBadge.textContent = `версия ${doc.version}`;
        say(box, `Сохранено, версия ${doc.version}.`);
      }),
    }, editor, h("div", { class: "row" }, h("button", { class: "button", type: "submit" }, "Сохранить версию"))));
  }

  async function showVersions() {
    const { data } = await api("GET", `${url}/versions`);
    const choices = data.map((item) => [String(item.version), `Версия ${item.version}`]);
    const base = select({ id: "diff-base" }, choices, choices[Math.min(1, choices.length - 1)][0]);
    const target = select({ id: "diff-target" }, choices, choices[0][0]);
    const diffBox = h("div", { id: "diff" });
    content.replaceChildren(h("div", { class: "card" },
      h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ["Версия", "Заголовок", "Сохранена", ""].map((name) => h("th", {}, name)))),
        h("tbody", {}, data.map((item) => h("tr", {},
          h("td", {}, String(item.version)),
          h("td", {}, item.title),
          h("td", { class: "muted" }, formatDate(item.created_at)),
          h("td", {}, canWrite && item.version !== doc.version && h("button", {
            class: "button secondary small", type: "button", "data-restore": item.version,
            onclick: guarded(box, async () => {
              doc = (await api("POST", `${url}/versions/${item.version}/restore`)).data;
              title.textContent = doc.title;
              versionBadge.textContent = `версия ${doc.version}`;
              say(box, `Версия ${item.version} возвращена как новая версия ${doc.version}.`);
              await showVersions();
            }),
          }, "Вернуть"))))))),
      h("h2", {}, "Сравнение версий"),
      h("div", { class: "row" }, base, "→", target, h("button", {
        class: "button secondary", type: "button", id: "diff-run",
        onclick: guarded(box, async () => {
          const result = await api("GET", `${url}/diff`, { params: { base: base.value, target: target.value } });
          diffBox.replaceChildren(diffView(result.data.diff));
        }),
      }, "Сравнить")),
      diffBox));
  }

  async function showShares() {
    const { data } = await api("GET", `${url}/shares`);
    const email = h("input", { type: "email", name: "share-email", required: true, placeholder: "адрес пользователя" });
    const permission = select({ name: "share-permission" }, [["read", "Чтение"], ["write", "Правка"]], "read");
    content.replaceChildren(h("div", { class: "card" },
      data.length ? h("table", {},
        h("thead", {}, h("tr", {}, ["Пользователь", "Доступ", ""].map((name) => h("th", {}, name)))),
        h("tbody", {}, data.map((share) => h("tr", {},
          h("td", {}, share.email),
          h("td", {}, ACCESS_NAMES[share.permission]),
          h("td", {}, h("button", {
            class: "button secondary small", type: "button",
            onclick: guarded(box, async () => {
              await api("DELETE", `${url}/shares/${share.user_id}`);
              await showShares();
            }),
          }, "Отозвать")))))) : h("p", { class: "muted" }, "Документ виден только вам"),
      h("h2", {}, "Открыть доступ"),
      h("form", {
        class: "row",
        onsubmit: guarded(box, async () => {
          await api("PUT", `${url}/shares`, { body: { email: email.value, permission: permission.value } });
          await showShares();
          say(box, "Доступ открыт.");
        }),
      }, email, permission, h("button", { class: "button", type: "submit" }, "Открыть"))));
  }

  const versionBadge = h("span", { class: "badge", id: "doc-version" }, `версия ${doc.version}`);
  const giveUp = !isOwner && h("button", {
    class: "button secondary small", type: "button",
    onclick: guarded(box, async () => {
      await api("DELETE", `${url}/shares/${state.user.id}`);
      location.hash = "#/documents";
    }),
  }, "Отказаться от доступа");
  const remove = isOwner && h("button", {
    class: "button danger small", type: "button", id: "delete-document",
    onclick: guarded(box, async () => {
      if (!confirm("Удалить документ вместе со всеми версиями?")) return;
      await api("DELETE", url);
      location.hash = "#/documents";
    }),
  }, "Удалить");

  show(false, h("div", { class: "row spread" }, title, h("div", { class: "row" }, remove, giveUp)),
    h("div", { class: "row" }, versionBadge,
      h("span", { class: "badge" }, `доступ: ${ACCESS_NAMES[doc.access]}`), tagBadges(doc.tags),
      doc.category && h("span", { class: "badge" }, doc.category)),
    h("p", {}), box, h("div", { class: "tabs" }, buttons), content);
  buttons[0].click();
}

function diffView(diff) {
  const lines = diff.split("\n").map((line, position) => {
    let className = null;
    // Первые две строки называют версии, а не показывают изменения.
    if (position < 2 || line.startsWith("@@")) className = "hunk";
    else if (line.startsWith("+")) className = "add";
    else if (line.startsWith("-")) className = "del";
    return h("span", { class: className || "line" }, `${line}\n`);
  });
  return h("pre", { class: "diff" }, diff ? lines : "Версии не различаются");
}

// --- Статистика

function barChart(items) {
  const width = 720;
  const rowHeight = 28;
  const labelWidth = 200;
  const namespace = "http://www.w3.org/2000/svg";
  const largest = Math.max(...items.map((item) => item.count));
  const svg = document.createElementNS(namespace, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${items.length * rowHeight}`);
  svg.setAttribute("class", "bars");
  svg.setAttribute("role", "img");
  items.forEach((item, position) => {
    const y = position * rowHeight;
    const bar = document.createElementNS(namespace, "rect");
    bar.setAttribute("x", labelWidth);
    bar.setAttribute("y", y + 5);
    bar.setAttribute("height", rowHeight - 10);
    bar.setAttribute("rx", 3);
    bar.setAttribute("width", Math.max(2, ((width - labelWidth - 50) * item.count) / largest));
    const label = document.createElementNS(namespace, "text");
    label.setAttribute("x", labelWidth - 8);
    label.setAttribute("y", y + 19);
    label.setAttribute("text-anchor", "end");
    label.textContent = item.name;
    const count = document.createElementNS(namespace, "text");
    count.setAttribute("x", labelWidth + ((width - labelWidth - 50) * item.count) / largest + 8);
    count.setAttribute("y", y + 19);
    count.textContent = item.count;
    svg.append(bar, label, count);
  });
  return svg;
}

async function statsPage() {
  const box = h("div", { class: "message", id: "message" });
  show(false, h("h1", {}, "Статистика"), box);
  await guarded(box, async () => {
    const { data } = await api("GET", "/stats");
    const cards = [
      ["Документов", data.documents],
      ["Версий", data.versions],
      ["Размер базы", formatSize(data.content_bytes)],
      ["Слов в индексе", data.indexed_terms],
      ["Попаданий в кэш", data.cache_hits],
      ["Промахов кэша", data.cache_misses],
    ];
    page.append(
      h("div", { class: "stat-cards", id: "stat-cards" }, cards.map(([name, value]) => h("div", { class: "stat-card" },
        h("div", { class: "stat-value" }, String(value)), h("div", { class: "muted" }, name)))),
      h("div", { class: "card" }, h("h2", {}, "Частые теги"),
        data.top_tags.length ? barChart(data.top_tags) : h("p", { class: "muted" }, "Тегов пока нет")),
      h("div", { class: "card" }, h("h2", {}, "Категории"),
        data.categories.length ? barChart(data.categories) : h("p", { class: "muted" }, "Категорий пока нет")),
    );
  })();
}

// --- Учётная запись

async function accountPage() {
  const box = h("div", { class: "message", id: "message" });
  const current = h("input", { type: "password", name: "current-password", required: true, autocomplete: "current-password" });
  const fresh = h("input", { type: "password", name: "new-password", required: true, autocomplete: "new-password" });
  const confirmPassword = h("input", { type: "password", name: "delete-password", required: true, autocomplete: "current-password" });
  await loadUser();
  const user = state.user;
  show(false, h("h1", {}, "Учётная запись"), box,
    h("div", { class: "card", id: "personal-data" }, h("h2", {}, "Что о вас хранится"),
      h("table", {}, h("tbody", {},
        h("tr", {}, h("th", {}, "Адрес"), h("td", {}, user.email)),
        h("tr", {}, h("th", {}, "Адрес подтверждён"), h("td", {}, formatDate(user.email_verified_at))),
        h("tr", {}, h("th", {}, "Согласие получено"), h("td", {}, formatDate(user.consent_at))),
        h("tr", {}, h("th", {}, "Регистрация"), h("td", {}, formatDate(user.created_at))))),
      h("p", { class: "muted" }, "Пароль хранится только в виде хэша Argon2id. Других персональных данных сервис не собирает.")),
    h("form", {
      class: "card",
      onsubmit: guarded(box, async () => {
        const { data } = await api("POST", "/auth/password/change", {
          body: { current_password: current.value, new_password: fresh.value },
        });
        state.token = data.access_token;
        current.value = "";
        fresh.value = "";
        say(box, "Пароль изменён, сеансы на других устройствах завершены.");
      }),
    }, h("h2", {}, "Смена пароля"), h("label", {}, "Текущий пароль", current), h("label", {}, "Новый пароль", fresh),
    h("button", { class: "button", type: "submit" }, "Сменить пароль")),
    h("form", {
      class: "card",
      onsubmit: guarded(box, async () => {
        if (!confirm("Удалить учётную запись и все документы без возможности восстановления?")) return;
        await api("POST", "/auth/me/delete", { body: { password: confirmPassword.value } });
        state.token = null;
        state.user = null;
        location.hash = "#/login";
      }),
    }, h("h2", {}, "Удаление учётной записи"),
    h("p", { class: "muted" }, "Вместе с учётной записью будут удалены все ваши документы и их версии."),
    h("label", {}, "Пароль для подтверждения", confirmPassword),
    h("button", { class: "button danger", type: "submit" }, "Удалить учётную запись")));
}

async function loadUser() {
  const { data } = await api("GET", "/auth/me");
  state.user = data;
  document.getElementById("user-email").textContent = data.email;
}

// --- Маршруты

const PUBLIC_ROUTES = {
  login: loginPage,
  register: registerPage,
  verify: verifyPage,
  forgot: forgotPage,
  reset: resetPage,
};
// Ссылки из писем работают и при открытом сеансе: они относятся к адресу, а не к сеансу.
const ONE_TIME_ROUTES = ["verify", "reset"];
const PRIVATE_ROUTES = {
  search: searchPage,
  documents: documentsPage,
  new: newDocumentPage,
  stats: statsPage,
  account: accountPage,
};

async function route() {
  const path = location.hash.replace(/^#\//, "").split("?")[0];
  const [name, argument] = path.split("/");

  if (state.token && !ONE_TIME_ROUTES.includes(name) && PUBLIC_ROUTES[name]) {
    location.hash = "#/search";
    return;
  }
  if (PUBLIC_ROUTES[name]) {
    await PUBLIC_ROUTES[name]();
    return;
  }
  if (!state.token) {
    location.hash = "#/login";
    return;
  }
  if (!state.user) await loadUser();

  for (const link of document.querySelectorAll(".nav a")) {
    const section = name === "doc" || name === "new" ? "documents" : name;
    link.classList.toggle("active", link.dataset.section === section);
  }
  if (name === "doc" && argument) await documentPage(argument);
  else if (PRIVATE_ROUTES[name]) await PRIVATE_ROUTES[name]();
  else location.hash = "#/search";
}

document.getElementById("logout").addEventListener("click", async () => {
  try {
    await api("POST", "/auth/logout");
  } finally {
    state.token = null;
    state.user = null;
    header.hidden = true;
    location.hash = "#/login";
  }
});

window.addEventListener("hashchange", route);

// Сеанс восстанавливается по cookie, поэтому токен доступа не нужно хранить между загрузками.
(async () => {
  const path = location.hash.replace(/^#\//, "").split("?")[0].split("/")[0];
  if (!ONE_TIME_ROUTES.includes(path)) await refresh();
  await route();
})();
