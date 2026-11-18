const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function main() {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', textContent: '', hidden: false, disabled: false, children: [], dataset: {},
      classList: { toggle() {} }, options: ['auto', '水彩', 'custom'].map(value => ({value})),
      events: {}, addEventListener(name, action) { this.events[name] = action; }, replaceChildren() { this.children = []; },
      append(...children) { this.children.push(...children); }, setAttribute() {}, focus() {},
    });
    return elements.get(id);
  }
  let posts = [];
  let tick;
  let pauseList = false;
  let releaseList;
  let copied;
  let reloadAccepted = false;
  let copyRequestUrl;
  const context = {
    document: { getElementById: element, querySelectorAll: () => [], createElement: () => element(Symbol()) },
    window: { addEventListener() {}, confirm: () => reloadAccepted },
    navigator: { clipboard: { writeText: async text => { copied = text; } } },
    fetch: async (url, options) => {
      if (pauseList && url === '/api/posts') await new Promise(resolve => { releaseList = resolve; });
      if (options.method === 'POST') return {
        status: 409, ok: false, json: async () => ({ ok: false, error: '另一窗口更新' }),
      };
      if (url.includes('/image-request?')) {
        copyRequestUrl = url;
        if (!url.endsWith(`revision=${posts[0].revision}`)) return {
          status: 409, ok: false, json: async () => ({ ok: false, error: '文案已在另一窗口更新' }),
        };
      }
      const data = url.includes('/image-request?') ? {
        cli_prefix: "python3 '/path with space/workflow.py' --repo-root '/isolated repo' --obsidian-dir '/isolated vault'",
        request_id: 'request-123',
      } : url === '/api/posts' ? posts : posts.find(post => url.endsWith(post.id));
      return { ok: true, json: async () => ({ok: true, data}) };
    },
    setInterval: callback => { tick = callback; },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8'), context);
  await new Promise(setImmediate);
  assert.equal(element('post-count').textContent, 0);
  posts = [{id:'external-post', title:'Codex 新建草稿', updated_at:'2026-11-12', status:'archived',
    revision:1, body:'原正文', active_version:1, confirmed_version:1, style:'auto', versions:[{number:1,body:'原正文'}],
    images:[], archive:{ok:true,repo_saved:true,obsidian_saved:true}}];
  await tick();
  assert.equal(element('post-count').textContent, 1, '页面没有选中稿件时，也应同步外部新草稿列表');
  assert.equal(element('post-list').children.length, 1);
  assert.equal(element('body').value, '', '同步列表不应擅自替换用户的新建编辑区');
  await element('post-list').children[0].events.click();
  await element('image-request').events.click();
  assert.ok(copied.includes("--repo-root '/isolated repo'"), '复制请求必须保留仓库目录');
  assert.ok(copied.includes("--obsidian-dir '/isolated vault'"), '复制请求必须保留月文件目录');
  assert.ok(copied.includes("'/path with space/workflow.py'") && copied.includes(' image-request --id external-post'));
  assert.match(copyRequestUrl, /[?]revision=1$/);
  assert.match(copied, /image-request --id external-post --revision 1/);
  pauseList = true;
  const pending = tick();
  element('body').value = '用户尚未保存的修改';
  element('body').events.input();
  posts = [{...posts[0],revision:2,body:'另一窗口更新'}];
  releaseList();
  await pending;
  assert.equal(element('body').value, '用户尚未保存的修改', '在途轮询不能覆盖用户新输入');
  assert.equal(element('save-status').textContent, '有未保存的修改');
  assert.equal(element('download-text').hidden, true);
  pauseList = false;
  await element('save').events.click();
  assert.equal(element('reload-post').hidden, false);
  await element('reload-post').events.click();
  assert.equal(element('body').value, '用户尚未保存的修改');
  reloadAccepted = true;
  await element('reload-post').events.click();
  assert.equal(element('body').value, '另一窗口更新');
  assert.equal(element('reload-post').hidden, true);
  assert.match(element('download-text').href, /[?]revision=2$/);
  element('image-file').events.click();
  posts = [{...posts[0], revision: 3, body: '选图时另一窗口更新'}];
  await tick();
  assert.equal(element('body').value, '选图时另一窗口更新');
  element('image-file').files = [{size: 100}];
  await element('image-file').events.change();
  assert.match(copyRequestUrl, /[?]revision=2$/, '打开选择器时锁定文案修订号');
  assert.equal(element('notice').textContent, '文案已在另一窗口更新');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
