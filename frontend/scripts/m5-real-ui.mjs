// Real Edge + production workbench. No route/model fixtures; exact-hash approvals.
import {chromium,expect} from '@playwright/test';
import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
const dir=new URL('../../.local/m5-acceptance/',import.meta.url),file=new URL('real-ui.json',dir);
const command=process.argv[2],which=process.argv[3];
const state=await fs.readFile(file,'utf8').then(JSON.parse).catch(()=>({cases:{},mutations:[]}));
const common=`交付完整可离线运行的中文静态应用，不添加账号、后端、网络或额外依赖。所有角色总预算30次模型调用，禁止扩额或重复建项目；批量write_files写源码，真实run_command测试/构建、Git提交，DeveloperResult引用真实成功的check_tool_call_ids。QA通过真实浏览器覆盖批准AC，独立Reviewer检查同一固定候选。测试接口调用生产业务逻辑，不能返回固定通过标记。`;
const cases={
 game:{name:'M5真实验收 · 2048',type:'browser_game',requirement:`${common}
一个Game Developer任务完成中文2048，TypeScript5.9.3/Vite7.3.7/原生DOM/CSS，无其他包。
REQ-001：4×4棋盘，开始生成2个2或4；方向键/四个触屏按钮“向上”“向下”“向左”“向右”移动；相邻相同数字按移动方向合并，一格一次移动只合并一次（2,2,2,2向左为4,4,0,0），分数累计本次合并值。有效移动后仅新增一个2或4，无效移动不新增。
REQ-002：带可访问名称“当前分数”“最高分”的output；“重新开始”清零分数并重置棋盘，最高分保留，最高分用localStorage保存；损坏或不可用存储显示中文提示、仍可内存游玩。
REQ-003：达到2048显示中文获胜提示并可继续，满盘无任何可合并邻居时显示游戏结束、可重开；按钮/按键正常，方向键不滚动页面。
REQ-004：390px无横向溢出，棋盘数字/控件有可访问名称。提供window.gameTest.getState()返回board/score/best/won/over的副本，setBoard(board,score=0)只接受4×4合法数字并使用同一生产状态，move(direction)、reset()调用真实逻辑，用于QA布置合并、胜利、结束边界，不改变其他行为。实际键盘和点击仍必须测试。
真实Node22原生TypeScript规则单测node --experimental-strip-types --test；tsconfig允许.ts导入/noEmit。prepare_dependencies固定锁文件，构建tsc --noEmit && vite build --configLoader runner --base ./；忽略node_modules、dist、.npm（无尾斜杠）。`},
 web:{name:'M5真实验收 · 事项清单',type:'web_app',requirement:`${common}
开发小型中文事项清单，纯静态HTML/CSS/原生JavaScript ES模块，无npm依赖。
REQ-001：带label“事项内容”的输入框和“添加事项”按钮，去除首尾空白；空白提交不新增、显示中文提示并聚焦输入框；有效添加创建唯一ID、标题纯文本显示并清空输入。
REQ-002：每条事项有可访问名称包含标题的“切换完成”控件与“删除”按钮；切换只影响当前条，删除只删除指定条。筛选按钮“全部”“未完成”“已完成”有真实选中状态；无事项时显示中文空状态。
REQ-003：localStorage保存事项，刷新恢复；读异常/损坏/无效结构恢复安全初始状态并提示；写异常时内存操作正常并提示，存储恢复后下一次操作能保存当前状态；文本不能作为HTML执行。
REQ-004：390px无横向溢出，label/键盘Tab/Enter可操作；Node22单测真实业务函数覆盖空白、唯一ID、切换/删除/筛选、坏存储及写失败。
架构合理拆成三个Web任务：T1准备静态入口、共享接口/最小模块骨架、忽略dist，明确API；T2与T3均依赖T1且各自范围不重叠，可实际并行。T2完成纯业务模型/存储和单测，T3完成完整界面绑定/CSS/可访问性，按批准接口消费T2。T1已提供可构建入口和接口骨架，T2/T3必须分别实际校验，但最终真实业务验收在全部集成后，骨架不得当最终通过。明确scope、interfaces、非目标和所有权；T1初始骨架范围与下游重叠仅沿依赖顺序，不允许下游互改。纯静态策略由平台构建，无package.json；Node测试用.mjs或明确ES模块扩展，避免浏览器/Node模块模式不一致。`}
};
if(!cases[which])throw Error('Only game/web acceptance cases permitted');
const entry=state.cases[which]??={};
const get=async path=>{const r=await fetch('http://127.0.0.1:5175/api/v1'+path);if(!r.ok)throw Error(`HTTP ${r.status}`);return r.json()};
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
 const context=await browser.newContext({viewport:{width:1440,height:1050}}),page=await context.newPage();
 page.on('request',r=>{if(r.method()!=='GET')state.mutations.push({case:which,method:r.method(),path:new URL(r.url()).pathname,time:new Date().toISOString()})});
 await page.goto('http://127.0.0.1:5175/');
 if(command==='create'){
  if(entry.project_id)throw Error('Existing project retained; no recreation');
  const projects=await get('/projects');if(projects.some(p=>p.name===cases[which].name)||projects.length>=2)throw Error('Inspect existing projects before any create');
  await page.getByRole('button',{name:'＋ 新建项目'}).click();
  await page.getByLabel('项目名称',{exact:true}).fill(cases[which].name);
  await page.getByRole('combobox',{name:/项目类型/}).selectOption(cases[which].type);
  await page.getByLabel('描述你的需求',{exact:true}).fill(cases[which].requirement);
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/api/v1/projects'));
  await page.getByRole('button',{name:'创建项目',exact:true}).click();
  entry.project_id=(await (await response).json()).id;
  const started=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/runs'));
  await page.getByRole('button',{name:'启动需求分析',exact:true}).click();
  entry.run_id=(await (await started).json()).id;
  entry.requirement_hash=createHash('sha256').update(cases[which].requirement).digest('hex');
 }else{
  if(!entry.project_id)throw Error('Create exact case first');
  await page.getByRole('combobox',{name:'当前项目'}).selectOption(entry.project_id);
  if(['read','approve','revise'].includes(command)){
   await page.getByRole('navigation',{name:'项目内容分类'}).getByRole('button',{name:/需求与审批/}).click();
   const docs=await get(`/projects/${entry.project_id}/documents`),approvals=await get(`/projects/${entry.project_id}/approvals/pending`);
   if(approvals.length!==1)throw Error('One pending approval required');
   const doc=docs.find(d=>d.id===approvals[0].document_id);entry.document=doc;
   await expect(page.getByRole('heading',{name:doc.kind==='prd'?`产品需求文档 v${doc.version}`:`技术架构 v${doc.version}`,exact:true})).toBeVisible();
   await fs.writeFile(new URL(`${which}-${doc.kind}-v${doc.version}.md`,dir),doc.markdown);
   await fs.writeFile(new URL(`${which}-${doc.kind}-v${doc.version}.json`,dir),JSON.stringify(doc,null,2));
   if(command!=='read'){
    if(process.argv[4]!==doc.content_hash)throw Error('Exact inspected document hash required');
    if(command==='revise')await page.getByLabel('修改意见',{exact:true}).fill(process.argv[5]||'');
    const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().includes(`/approvals/${approvals[0].id}/`));
    await page.getByRole('button',{name:command==='revise'?'要求修改':'批准此版本',exact:true}).click();
    expect((await response).ok()).toBeTruthy();
    entry.decisions=[...(entry.decisions||[]),{id:doc.id,hash:doc.content_hash,version:doc.version,kind:doc.kind,decision:command}];
   }
  }else if(command==='inspect'){
   const run=(await get(`/projects/${entry.project_id}/runs`)).find(r=>r.id===entry.run_id);entry.run=run;
   entry.candidates=await get(`/runs/${entry.run_id}/candidates`);entry.deliveries=await get(`/runs/${entry.run_id}/deliveries`);
  }else throw Error('Unsupported command');
 }
 await page.screenshot({path:new URL(`${which}-${command}.png`,dir).pathname.replace(/^\/([A-Za-z]:)/,'$1')});
}finally{await fs.writeFile(file,JSON.stringify(state,null,2));await browser.close()}
console.log(JSON.stringify({case:which,project_id:entry.project_id,run_id:entry.run_id,document:entry.document?{kind:entry.document.kind,version:entry.document.version,hash:entry.document.content_hash}:null,run:entry.run}));
