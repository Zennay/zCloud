#!/usr/bin/env node
// Reattach the temporary worker extension to an ALREADY RUNNING Firefox.
// Never spawn a Firefox process, window, tab, or change browser focus.
import {execFileSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {DEFAULT_ADDON_ID,parseDebuggerPort,remoteModulePath} from './zcloud_reload_extension.mjs';

async function ensureWorkerAddon({
  addonId=DEFAULT_ADDON_ID,
  addonPath=(process.env.HOME||'/home/ubuntu')+'/snap/firefox/common/chatgpt-project-extension',
  home=process.env.HOME||'/home/ubuntu',
  processList=null,
}={}){
  const ps=processList??execFileSync('ps',['-eo','args='],{encoding:'utf8'});
  const port=parseDebuggerPort(ps);
  const {connect}=await import(pathToFileURL(remoteModulePath(home)).href);
  const remote=await connect(port);
  try{
    try{
      const addon=await remote.getInstalledAddon(addonId);
      await remote.reloadAddon(addonId);
      return {ok:true,action:'reloaded-existing-temporary-addon',port,addon_id:addon.id};
    }catch(err){
      if(!/does not have your extension installed/i.test(String(err?.message||err)))throw err;
    }
    // A Firefox crash drops temporary add-ons when the browser is restarted
    // directly. Reinstall the exact project-runner add-on in-place instead of
    // killing Firefox or opening a second browser with web-ext.
    const result=await remote.installTemporaryAddon(addonPath,false);
    return {ok:true,action:'installed-missing-temporary-addon',port,
      addon_id:result?.addon?.id||result?.id||addonId};
  }finally{remote.disconnect()}
}

export {ensureWorkerAddon};
if(import.meta.url===pathToFileURL(process.argv[1]).href){
  ensureWorkerAddon().then(r=>console.log(JSON.stringify(r)))
    .catch(e=>{console.error(e?.stack||String(e));process.exitCode=1});
}
