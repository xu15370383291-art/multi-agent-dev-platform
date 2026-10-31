// Explicitly invoked real-model acceptance driver. No mocks; never runs in the unit test suite.
import { chromium } from '@playwright/test';
import fs from 'node:fs/promises';
import { createHash } from 'node:crypto';
const root=new URL('../../.local/',import.meta.url);
const statePath=new URL('m1-live.json',root);
await fs.mkdir(root,{recursive:true});
const command=process.argv[2]||'inspect';
const browser=await chromium.launch({channel:'msedge',headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1050}});
page.setDefaultTimeout(15000);
const call=async(path)=>{
  const response=await fetch('http://127.0.0.1:8000/api/v1'+path);
  if(!response.ok)throw new Error('API '+response.status);
  return response.json();
};
try{
  let state=await fs.readFile(statePath,'utf8').then(JSON.parse).catch(()=>({}));
  await page.goto('http://127.0.0.1:5173/');
  if(command==='configure'){
    await page.getByRole('button',{name:'模型配置'}).click();
    let card=page.locator('.model-item').filter({has:page.getByText('阿里模型',{exact:true})});
    await card.getByRole('button',{name:'编辑',exact:true}).click();
    await page.getByLabel('最大输出 Token（含思考）').fill('16384');
    await page.getByLabel('请求超时（秒）').fill('180');
    await page.getByLabel('思考 Token 上限（可选）').fill('2048');
    await page.getByRole('button',{name:'保存配置',exact:true}).click();
    await page.getByText('模型配置已保存，密钥存放于 Windows 凭据管理器。请测试能力后再启动项目。',{exact:true}).waitFor();
    card=page.locator('.model-item').filter({has:page.getByText('阿里模型',{exact:true})});
    const [response]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/validate'),{timeout:240000}),card.getByRole('button',{name:'测试能力',exact:true}).click()]);
    const profile=await response.json();
    console.log(JSON.stringify({name:profile.name,revision:profile.revision,max_output_tokens:profile.max_output_tokens,timeout_seconds:profile.timeout_seconds,capabilities:profile.capabilities},null,2));
  }else if(command==='create'){
    if(state.project_id)throw new Error('Existing live acceptance project recorded; inspect it first');
    const requirement=`正式 M1 验收项目：单人浏览器 2048，严格完成以下需求，不缩减或伪造验收。
REQ-001/AC-001：4×4 棋盘，箭头键和 WASD 四方向输入；无效移动不生成新块。
REQ-002/AC-002：同值合并，每块单次移动最多合并一次；[2,2,2,2] 左移成 [4,4,0,0]（生成新块前）。验证四方向。
REQ-003/AC-003：初始两个方块，有效移动后只在空位生成一个2或4；可以注入确定随机源验证位置和值。
REQ-004/AC-004：分数按合并结果累计；最高分 localStorage 刷新保留；存储读取或写入抛异常时仍能正常游戏。
REQ-005/AC-005：满盘且四方向无移动才结束；有空位或相邻相同方块不能误判；重新开始重置棋盘和本局分数，保留最高分。达到2048提示胜利但可继续游戏。
REQ-006/AC-006：手机触摸滑动支持四方向；短距离不误触、无效移动不生块；390px 宽度无横向溢出，中文界面、说明与重开按钮可用。
技术约束：TypeScript + Vite + DOM/CSS，无后端、账号、在线资源或其他 npm 包；使用平台固定工具链和锁文件，独立单个 game_developer 任务覆盖全部 AC。提供 README 运行说明和实际规则单元检查。
工程验收接口：生产构建提供确定性 window.gameTest（setState/getState/move，可注入 RNG），供 QA 操作同一规则和 UI；规则函数与实际键盘/触摸复用，不设置假通过标记。测试接口需说明参数和返回值，不破坏正常玩法。
必须真实构建、独立浏览器 QA 与 Reviewer 检查同一 SHA 后，由平台门槛交付 ZIP、报告和可用预览。`;
    await page.getByRole('button',{name:'＋ 新建项目'}).click();
    await page.getByLabel('项目名称').fill('正式 2048 · M1 验收');
    await page.getByLabel('描述你的需求').fill(requirement);
    await page.getByRole('button',{name:'创建项目',exact:true}).click();
    await page.getByRole('heading',{name:'正式 2048 · M1 验收',exact:true}).waitFor();
    const projects=await call('/projects');
    const project=projects.find(p=>p.name==='正式 2048 · M1 验收');
    state={project_id:project.id,created_at:new Date().toISOString()};
    await fs.writeFile(statePath,JSON.stringify(state,null,2));
    await page.getByRole('button',{name:'启动需求分析'}).click();
    console.log('Created and started real acceptance project',state.project_id);
  }else{
    if(!state.project_id)throw new Error('No acceptance project');
    const projects=await call('/projects');
    const project=projects.find(p=>p.id===state.project_id);
    await page.getByRole('button',{name:new RegExp(project.name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'))}).click();
    if(command==='approve'||command==='revise'){
      const approvals=await call(`/projects/${state.project_id}/approvals/pending`);
      if(approvals.length!==1)throw new Error('Exactly one pending approval required');
      const docs=await call(`/projects/${state.project_id}/documents`);
      const doc=docs.find(d=>d.id===approvals[0].document_id);
      if(process.argv[3]!==doc.content_hash)throw new Error('Pass the inspected document hash to approve or revise this exact version');
      if(command==='revise'){
        const feedback=process.argv[4];if(!feedback)throw new Error('Feedback required');
        await page.getByPlaceholder('要求修改时必填').fill(feedback);
        await page.getByRole('button',{name:'要求修改',exact:true}).click();
      }else await page.getByRole('button',{name:'批准此版本',exact:true}).click();
      console.log('Submitted',command,doc.kind,doc.version,doc.content_hash);
    }
    const runs=await call(`/projects/${state.project_id}/runs`);
    const docs=await call(`/projects/${state.project_id}/documents`);
    if(command==='inspect'){
      const latest=docs.filter(d=>d.run_id===runs[0]?.id && (!process.argv[3] || d.kind===process.argv[3]) && !docs.some(other=>other.run_id===d.run_id && other.kind===d.kind && other.version>d.version));
      console.log(JSON.stringify({runs:runs.slice(0,1),documents:latest.map(({payload,...doc})=>({...doc,criteria:payload.criteria,tasks:payload.tasks,open_questions:payload.open_questions}))},null,2));
    }else console.log(JSON.stringify({runs:runs.slice(0,1)},null,2));
    if(command==='start'){
      await page.getByRole('button',{name:'启动需求分析'}).click();
      console.log('Started new run after prior run ended');
    }
    if(command==='cancel')await page.getByRole('button',{name:'取消运行',exact:true}).click();
    if(command==='resume')await page.getByRole('button',{name:'核对后继续开发',exact:true}).click();
    if(command==='revise-quality'||command==='recheck-quality'){
      const reports=await call(`/runs/${runs[0].id}/quality-reports`);
      const qa=[...reports].reverse().find(r=>r.role==='qa'), reviewer=[...reports].reverse().find(r=>r.role==='reviewer');
      if(qa?.id!==process.argv[3]||reviewer?.id!==process.argv[4]||!process.argv[5])throw new Error('Pass the inspected QA/reviewer IDs and revision feedback');
      await page.getByLabel('测试脚本修订意见',{exact:true}).fill(process.argv[5]);
      await page.getByRole('button',{name:command==='recheck-quality'?'原测试重新验证':'修订测试并重新验证',exact:true}).click();
      await page.getByText(command==='recheck-quality'?'原测试重验已入队，脚本逐字保留，重新执行并独立审查，原预算不变。':'新测试资产验证已入队，旧报告保留，原运行预算不变。',{exact:true}).waitFor();
    }
    if(command==='delivery'){
      if(runs[0]?.status!=='succeeded'||runs[0]?.phase!=='delivered')throw new Error('Latest run must actually be delivered');
      const deliveries=await call(`/runs/${runs[0].id}/deliveries`);
      if(deliveries.length!==1)throw new Error('One ready delivery required');
      await page.getByRole('heading',{name:'项目交付',exact:true}).waitFor();
      await page.getByRole('heading',{name:'QA 浏览器验收',exact:true}).waitFor();
      await page.getByRole('heading',{name:'Reviewer 独立审查',exact:true}).waitFor();
      const summaries=page.getByText('查看报告与测试证据',{exact:true});
      if(await summaries.count()!==2)throw new Error('Both reports must be visible');
      for(const summary of await summaries.all())await summary.click();
      for(const reportId of [deliveries[0].manifest.qa_id,deliveries[0].manifest.reviewer_id]){
        if(!(await page.locator('details[open] pre').allTextContents()).some(text=>text.includes(reportId)))throw new Error('Workbench report does not show the delivered report ID');
      }
      const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('link',{name:'下载交付 ZIP',exact:true}).click()]);
      const dir=new URL('acceptance/',root);await fs.mkdir(dir,{recursive:true});
      const target=new URL('workbench-download.zip',dir);
      await download.saveAs(target.pathname.replace(/^\/([A-Za-z]:)/,'$1'));
      const digest=createHash('sha256').update(await fs.readFile(target)).digest('hex');
      if(digest!==deliveries[0].archive_hash)throw new Error('Workbench download digest mismatch');
      const [preview]=await Promise.all([page.waitForEvent('popup'),page.getByRole('link',{name:'打开游戏预览 ↗',exact:true}).click()]);
      await preview.waitForFunction(()=>!!window.gameTest);
      if(preview.url()!==deliveries[0].manifest.preview_url)throw new Error('Preview points at another delivery');
      const evidence={run_id:runs[0].id,delivery_id:deliveries[0].id,archive_hash:digest,preview_url:preview.url(),reports:2,status:'passed',time:new Date().toISOString()};
      await fs.writeFile(new URL('workbench.json',dir),JSON.stringify(evidence,null,2));
      console.log(JSON.stringify(evidence,null,2));
      for(const summary of await summaries.all())await summary.click();
      await page.evaluate(()=>window.scrollTo(0,0));
    }
  }
  await page.screenshot({path:new URL(`m1-${command}.png`,root).pathname.replace(/^\/([A-Za-z]:)/,'$1'),fullPage:command!=='delivery'});
}finally{await browser.close();}
