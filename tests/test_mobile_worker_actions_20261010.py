"""Regression checks for the worker-first mobile controls (no VPS writes)."""
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "public" / "app.js"


class MobileWorkerActionTests(unittest.TestCase):
    def test_play_targets_the_selected_project_or_actual_worker(self):
        script = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const boot=source.slice(0,source.indexOf('let draggedProject=null;'));
assert.ok(boot.length>50000);
const ctx=vm.createContext({window:{alert:()=>{}},setTimeout:()=>{}});
vm.runInContext(boot,ctx,{timeout:2500});
vm.runInContext('DATA={dynamic_workers:{chatgpt_count:3,claude_count:0},projects:[{id:"supa",name:"Supa"},{id:"lightup",name:"LightUp"}]}; WORKER_DEBUG={workers:[{global_slot:1,project:"supa",worker:"supa::w1",desired_state:"paused",verdict:"paused"},{global_slot:3,project:"lightup",worker:"lightup::w1",desired_state:"running",verdict:"generating"}]};',ctx);
const calls=[];
ctx.requestGlobalWorkerProject=async(slot,button)=>calls.push(['request',slot]);
ctx.controlWorker=async(id,action,button)=>calls.push(['worker',id,action]);
const button=(project)=>({closest:()=>({querySelector:()=>({value:project})})});
(async()=>{
  await ctx.playGlobalWorker(1,button(''));
  await ctx.playGlobalWorker(2,button('supa'));
  assert.deepEqual(calls,[['worker','supa::w1','start'],['request',2]]);
  const markup=vm.runInContext('globalWorkerRows()',ctx);
  assert.equal((markup.match(/data-global-worker-play=/g)||[]).length,3);
  assert.equal((markup.match(/data-worker-action="pause"/g)||[]).length,3);
  assert.equal((markup.match(/data-worker-action="push"/g)||[]).length,3);
  assert.match(markup,/data-global-worker-play="3"[^>]*disabled/);
  assert.match(markup,/Signaal:.*Prompt:.*Taak:/s);
  assert.match(markup,/Kies een project…/);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
        result=subprocess.run(
            ["node","-e",script,str(APP)],capture_output=True,text=True,timeout=20,check=False,
        )
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=="__main__":
    unittest.main()
