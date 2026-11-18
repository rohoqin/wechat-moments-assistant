const $ = id => document.getElementById(id);
const labels = { draft: "草稿", confirmed: "定稿 · 待补写", archived: "已归档", illustrated: "图文就绪" };
let active = null;
let dirty = false;
let busy = false;
let conflicted = false;
let listRequest = 0;
let uploadTarget = null;

function notice(text = "", error = false) {
  $("notice").textContent = text;
  $("notice").hidden = !text;
  $("notice").classList.toggle("error", error);
}

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const result = await response.json();
  if (!response.ok || !result.ok) {
    const error = new Error(result.error || "操作没有完成，请重试");
    error.status = response.status;
    throw error;
  }
  return result.data;
}

async function run(action) {
  if (busy) return;
  busy = true;
  controls();
  notice();
  try { await action(); } catch (error) {
    if (error.status === 409) conflicted = true;
    notice(error.message, true);
  }
  finally { busy = false; controls(); }
}

function styleValue() { return $("style").value === "custom" ? $("custom-style").value.trim() : $("style").value; }
function controls() {
  const archived = active && ["archived", "illustrated"].includes(active.status);
  $("reload-post").hidden = !active || !conflicted;
  $("reload-post").disabled = busy;
  $("download-text").hidden = !active || dirty || busy || conflicted;
  $("save").disabled = busy || !dirty;
  $("confirm").disabled = busy || !active || dirty || !$("body").value.trim();
  $("confirm").textContent = active?.status === "confirmed" ? "补写归档" : archived ? "核对归档" : "确认定稿并归档";
  $("image-request").disabled = busy || !archived || dirty;
  $("image-file").disabled = busy || !archived || dirty;
  $("upload-label").classList.toggle("disabled", busy || !archived || dirty);
  for (const id of ["title", "body", "style", "custom-style", "new-post"]) $(id).disabled = busy;
  $("save-status").textContent = busy ? "正在保存与核对…" : dirty ? "有未保存的修改" : active ? "已保存到本地" : "尚未保存";
  $("character-count").textContent = [...$("body").value.replace(/\s/g, "")].length;
  $("action-hint").textContent = dirty ? "先保存修改，再确认当前版本。修改正文后需要重新定稿。" : archived ? "当前正文已完成双份归档，可以继续配图。" : "确认定稿后，同时写入仓库与 Obsidian。";
  document.querySelectorAll(".version-button").forEach(button => { button.disabled = busy || dirty; });
}

function load(post) {
  active = post;
  dirty = false;
  conflicted = false;
  $("title").value = post?.title || "";
  $("body").value = post?.body || "";
  const style = post?.style || "auto";
  const known = [...$("style").options].some(option => option.value === style && style !== "custom");
  $("style").value = known ? style : "custom";
  $("custom-style").hidden = known;
  $("custom-style").value = known ? "" : style;
  $("status").textContent = post ? labels[post.status] : "草稿";
  $("status").dataset.state = post?.status || "draft";
  $("editor-label").textContent = post ? `当前版本 · v${post.active_version}` : "开始一篇新记录";
  $("version-count").textContent = post ? `${post.versions.length} 个版本` : "尚无版本";
  $("versions").replaceChildren();
  if (post) {
    [...post.versions].reverse().forEach(version => {
      const button = document.createElement("button");
      button.className = "version-button" + (version.number === post.active_version ? " active" : "");
      button.textContent = `v${version.number}` + (version.number === post.confirmed_version ? " · 定稿" : "");
      button.title = version.body.slice(0, 80);
      button.addEventListener("click", () => run(async () => {
        if (version.number === active.active_version) return;
        load(await api(`/api/posts/${active.id}`, { revision: active.revision, active_version: version.number }));
        await refreshList();
      }));
      $("versions").append(button);
    });
  } else {
    const span = document.createElement("span"); span.className = "empty-inline"; span.textContent = "保存后，修改记录会留在这里。"; $("versions").append(span);
  }
  const image = post?.current_image;
  $("image-stage").replaceChildren();
  if (image) {
    const link = document.createElement("a"); link.href = `/api/posts/${post.id}/images/${image.id}`; link.target = "_blank"; link.rel = "noopener";
    const img = document.createElement("img"); img.alt = `${post.title} · ${image.actual_style}`; img.src = link.href;
    link.append(img); $("image-stage").append(link);
    $("image-hint").textContent = `已关联 v${image.version} · ${image.actual_style}。点击图片查看原图。`;
  } else {
    const box = document.createElement("div"); box.className = "empty-visual";
    const symbol = document.createElement("div"); symbol.className = "visual-symbol"; symbol.setAttribute("aria-hidden", "true");
    for (let i = 0; i < 3; i++) symbol.append(document.createElement("span"));
    const h = document.createElement("h3"); h.textContent = post?.confirmed_version ? "给这段话配一张图" : "先把文案写好";
    const p = document.createElement("p"); p.textContent = post?.images.length ? "历史配图仍保留，当前版本需要新的画面。" : "定稿后，让画面接住这段话。";
    box.append(symbol, h, p); $("image-stage").append(box);
    $("image-hint").textContent = post?.image_error?.message || "确认并完成双份归档后，复制请求到 Codex 生成配图；结果会保存在这条文案下。";
  }
  const archive = post?.archive;
  $("repo-result").textContent = archive?.repo_saved ? "已保存 ✓" : archive ? "待补写" : "待定稿";
  $("vault-result").textContent = archive?.obsidian_saved ? "已保存 ✓" : archive ? "待补写" : "待定稿";
  $("archive-date").textContent = post?.confirmed_date || "—";
  $("archive-error").hidden = !archive?.error;
  $("archive-error").textContent = archive?.error || "";
  if (post) { $("download-text").href = `/api/posts/${post.id}/text?revision=${post.revision}`; $("download-text").download = `${post.title}.txt`; }
  controls();
}

async function refreshList() {
  const sequence = ++listRequest;
  const posts = await api("/api/posts");
  if (sequence !== listRequest) return posts;
  $("post-count").textContent = posts.length;
  $("post-list").replaceChildren();
  posts.forEach(post => {
    const button = document.createElement("button"); button.className = "post-item" + (post.id === active?.id ? " active" : "");
    const title = document.createElement("strong"); title.textContent = post.title;
    const meta = document.createElement("small"); const date = document.createElement("span"); date.textContent = post.updated_at.slice(5, 10).replace("-", "."); const state = document.createElement("span"); state.textContent = labels[post.status]; meta.append(date, state); button.append(title, meta);
    button.addEventListener("click", () => run(async () => {
      if (dirty) { notice("当前修改尚未保存，请先保存后再切换文案。", true); return; }
      load(await api(`/api/posts/${post.id}`)); await refreshList();
    }));
    $("post-list").append(button);
  });
  return posts;
}

for (const id of ["title", "body", "custom-style"]) $(id).addEventListener("input", () => { dirty = true; controls(); });
$("style").addEventListener("change", () => { $("custom-style").hidden = $("style").value !== "custom"; dirty = true; controls(); });
$("reload-post").addEventListener("click", () => {
  if (!active || (dirty && !window.confirm("载入最新稿会放弃本窗口未保存的修改。继续吗？"))) return;
  return run(async () => { load(await api(`/api/posts/${active.id}`)); await refreshList(); });
});
$("new-post").addEventListener("click", () => {
  if (dirty) return notice("当前修改尚未保存，请先保存后再新建。", true);
  notice(); load(null); refreshList().catch(error => notice(error.message, true)); $("title").focus();
});
$("save").addEventListener("click", () => run(async () => {
  const data = { title: $("title").value, body: $("body").value, style: styleValue() };
  const post = active ? await api(`/api/posts/${active.id}`, { ...data, revision: active.revision }) : await api("/api/posts", data);
  load(post); await refreshList(); notice("修改已保存。" + (post.confirmed_version ? "文案定稿状态保持有效。" : "请确认这版是否定稿。"));
}));
$("confirm").addEventListener("click", () => run(async () => {
  const post = await api(`/api/posts/${active.id}/confirm`, { revision: active.revision });
  load(post); await refreshList();
  notice(post.archive.ok ? "已确认定稿，仓库与 Obsidian 均已保存。" : "文案已确认，但有归档尚未完成，请按下方提示补写。", !post.archive.ok);
}));
async function copy(text) {
  try { await navigator.clipboard.writeText(text); }
  catch { const box = document.createElement("textarea"); box.value = text; document.body.append(box); box.select(); const done = document.execCommand("copy"); box.remove(); if (!done) throw new Error("浏览器未能复制，请允许剪贴板访问后重试"); }
}
$("image-request").addEventListener("click", () => run(async () => {
  const request = await api(`/api/posts/${active.id}/image-request?revision=${active.revision}`);
  await copy(`使用 $wechat-moments-assistant 为文案 ${active.id} 生成配图。先执行以下命令读取当前请求：\n${request.cli_prefix} image-request --id ${active.id} --revision ${active.revision}\n使用内置生图工具生成并查看图片，再使用同一命令前缀执行 attach-image，关联到请求 ${request.request_id}。该前缀的 --repo-root 与 --obsidian-dir 必须保留，不使用默认目录替代。当前风格：${active.style}。不要重新归档正文。`);
  notice("配图请求已复制。粘贴到本项目的 Codex 创作聊天执行；此操作尚未生成图片。");
}));
$("image-file").addEventListener("click", () => {
  uploadTarget = active && { id: active.id, revision: active.revision, style: active.style };
});
$("image-file").addEventListener("change", () => run(async () => {
  const file = $("image-file").files[0]; if (!file) return;
  const target = uploadTarget;
  uploadTarget = null;
  $("image-file").value = "";
  if (!target || target.id !== active?.id) throw new Error("当前文案已变更，请重新选择图片");
  if (file.size > 20 * 1024 * 1024) throw new Error("请选择 20 MB 以内的图片");
  const request = await api(`/api/posts/${target.id}/image-request?revision=${target.revision}`);
  const data = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(",")[1]); reader.onerror = () => reject(new Error("图片读取失败")); reader.readAsDataURL(file); });
  load(await api(`/api/posts/${target.id}/images`, { data, request_id: request.request_id, actual_style: target.style === "auto" ? "用户导入" : target.style }));
  await refreshList(); notice("图片已关联到当前定稿。正文没有重复归档。");
}));

run(async () => { const posts = await refreshList(); load(posts[0] || null); await refreshList(); });
setInterval(async () => {
  if (busy || dirty) return;
  const selected = active?.id;
  try {
    const posts = await refreshList();
    if (busy || dirty || !active || active.id !== selected) return;
    const latest = posts.find(post => post.id === selected);
    if (latest && latest.revision > active.revision) load(latest);
  }
  catch { /* 下次交互会显示可操作的网络错误。 */ }
}, 5000);
window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
