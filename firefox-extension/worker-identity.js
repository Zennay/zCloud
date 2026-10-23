// Restore only the extension-owned session identity; never infer it from chat URLs.
(async () => {
  const key = await browser.runtime.sendMessage({type:"zcloud-worker-identity"}).catch(()=>null);
  if(!/^[a-z0-9_-]+::w[1-9][0-9]*$/i.test(key||""))return;
  const prior=sessionStorage.getItem("zcloud-v2-worker");
  if(prior&&prior!==key)return;
  sessionStorage.setItem("zcloud-v2-worker",key);
  const signal=()=>document.documentElement?.dispatchEvent(new Event("zcloud-worker-identity-ready"));
  if(document.documentElement)signal();
  else document.addEventListener("DOMContentLoaded",signal,{once:true});
})();
