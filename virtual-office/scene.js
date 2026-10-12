import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.170.0/+esm';
export async function startOffice(ui){
 const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(65,1,.06,1400);
 const renderer=new THREE.WebGLRenderer({antialias:true,powerPreference:'high-performance'});
 renderer.setPixelRatio(Math.min(devicePixelRatio,1.7));renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;
 renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.15;
 ui.mount.append(renderer.domElement);const canvas=renderer.domElement;canvas.setAttribute('aria-label','Loop door je penthousekantoor');canvas.tabIndex=0;
 let seed=64;const rand=()=>{seed=(seed*1664525+1013904223)>>>0;return seed/4294967296};
 const materials=[],geometries=[],textures=[],colliders=[],targets=[],actors=[],keys=new Set(),listeners=[];
 const on=(el,event,fn)=>{el.addEventListener(event,fn);listeners.push(()=>el.removeEventListener(event,fn))};
 const material=(color,roughness=.65,metalness=0)=>{const m=new THREE.MeshStandardMaterial({color,roughness,metalness});materials.push(m);return m};
 const ink=material('#202d33',.37,.55),brass=material('#b49662',.26,.8),walnut=material('#584031',.53),lightWood=material('#ad8a61',.54),ivory=material('#ded4c3',.76),leather=material('#786049',.84),darkLeather=material('#243438',.85),stone=material('#bcbab0',.26,.16),green=material('#3f5742',.9),black=material('#121b23',.4),white=material('#e8e2d3',.7);
 const glow=material('#ffe2b3',.3);glow.emissive=new THREE.Color('#ffcb84');glow.emissiveIntensity=2.2;
 const glass=new THREE.MeshPhysicalMaterial({color:'#b8d3d6',transparent:true,opacity:.12,roughness:.055,metalness:.15,depthWrite:false});materials.push(glass);
 const boxGeo=new THREE.BoxGeometry(1,1,1),sphereGeo=new THREE.SphereGeometry(1,18,14),cylGeo=new THREE.CylinderGeometry(1,1,1,20);
 geometries.push(boxGeo,sphereGeo,cylGeo);
 function mesh(geo,m,x,y,z,sx=1,sy=1,sz=1,parent=scene){const o=new THREE.Mesh(geo,m);o.position.set(x,y,z);o.scale.set(sx,sy,sz);o.castShadow=true;o.receiveShadow=true;parent.add(o);return o}
 const box=(x,y,z,w,h,d,m,parent)=>mesh(boxGeo,m,x,y,z,w,h,d,parent);
 const ball=(x,y,z,rx,ry,rz,m,parent)=>mesh(sphereGeo,m,x,y,z,rx,ry,rz,parent);
 const cyl=(x,y,z,r,h,m,parent)=>mesh(cylGeo,m,x,y,z,r,h,r,parent);
 function collision(x,z,w,d){colliders.push({x,z,w:w/2+.28,d:d/2+.28})}
 function target(object,id){object.userData.action=id;targets.push(object);return object}
 function canvasTex(w,h,draw){const c=document.createElement('canvas');c.width=w;c.height=h;draw(c.getContext('2d'),w,h);const t=new THREE.CanvasTexture(c);t.colorSpace=THREE.SRGBColorSpace;textures.push(t);return t}
 const floorTexture=canvasTex(1024,1024,(ctx,w,h)=>{ctx.fillStyle='#c3bcad';ctx.fillRect(0,0,w,h);for(let i=0;i<950;i++){ctx.strokeStyle='rgba(91,78,66,'+(.015+rand()*.04)+')';ctx.beginPath();let y=rand()*h;ctx.moveTo(0,y);for(let x=0;x<=w;x+=64)ctx.lineTo(x,y+Math.sin(x*.01+i)*rand()*17);ctx.stroke()}ctx.strokeStyle='#8b827440';ctx.lineWidth=2;for(let i=0;i<=4;i++){ctx.beginPath();ctx.moveTo(i*256,0);ctx.lineTo(i*256,h);ctx.stroke();ctx.beginPath();ctx.moveTo(0,i*256);ctx.lineTo(w,i*256);ctx.stroke()}});floorTexture.wrapS=floorTexture.wrapT=THREE.RepeatWrapping;floorTexture.repeat.set(4,3);
 const floorMat=material('#ffffff',.31,.12);floorMat.map=floorTexture;
 box(0,-.14,0,38,.26,30,floorMat);
 // Dark walnut inset provides a clear walking spine and zoning.
 box(0,.001,-8,32,.03,10,walnut);for(let x=-15.5;x<16;x+=.65)box(x,.018,-8,.013,.01,10,ink);
 box(-11,.02,7,12,.035,10,material('#a5a392',.98));box(11.2,.025,7,11,.04,10,material('#777c78',.96));
 // Curtain wall, slender mullions and ceiling coffers.
 for(let x=-19;x<=19;x+=3.8){box(x,2.45,-15,.07,4.9,.11,ink);box(x,2.45,15,.07,4.9,.11,ink)}
 for(let z=-15;z<=15;z+=3){box(-19,2.45,z,.11,4.9,.07,ink);box(19,2.45,z,.11,4.9,.07,ink)}
 for(const z of [-15,15]){box(0,4.85,z,38,.13,.15,ink);box(0,.12,z,38,.24,.22,stone);box(0,2.8,z,38,.035,.075,ink);box(0,2.5,z,38,4.5,.018,glass)}
 for(const x of [-19,19]){box(x,4.85,0,.15,.13,30,ink);box(x,.12,0,.22,.24,30,stone);box(x,2.8,0,.075,.035,30,ink);box(x,2.5,0,.018,4.5,30,glass)}
 box(0,5.05,0,38,.25,30,material('#c8c3b6',.8));
 for(let x=-16;x<19;x+=6){box(x,4.8,0,.17,.23,30,walnut);for(let z=-12;z<15;z+=6){box(x+2,4.89,z,3,.035,.035,glow)}}
 // Core, gallery and glass partitions with actual walkable doorways.
 box(-.5,2.3,1.2,5.4,4.6,.3,walnut);collision(-.5,1.2,5.4,.3);
 for(let x=-3.1;x<=2.15;x+=.16)box(x,2.3,1.4,.035,4.6,.035,brass);
 function wallLabel(text,x,y,z,width){const tex=canvasTex(1024,180,(c)=>{c.fillStyle='#d4bd96';c.font='52px Georgia';c.textAlign='center';c.fillText(text,512,112)});const m=new THREE.MeshBasicMaterial({map:tex,transparent:true,depthWrite:false});materials.push(m);const p=new THREE.Mesh(new THREE.PlaneGeometry(width,width*180/1024),m);geometries.push(p.geometry);p.position.set(x,y,z);scene.add(p);return p}
 wallLabel('THE PENTHOUSE',-.5,3.05,1.59,3.6);
 for(const x of [-5,5]){for(const segment of [{z:6,w:6},{z:13,w:4}]){box(x,2.1,segment.z,.018,4.2,segment.w,glass);box(x,4.2,segment.z,.07,.07,segment.w,ink);collision(x,segment.z,.018,segment.w);box(x,1.05,segment.z,.022,.04,segment.w,brass);}for(const z of [3,9,11,15])box(x,2.1,z,.05,4.2,.06,ink)}
 // Door portals are deliberately open, with visible jambs and floor thresholds.
 for(const x of [-5,5]){box(x,4.2,10,.08,.08,2,brass);box(x,.018,10,.32,.025,2,brass);for(const z of [9,11])box(x,2.1,z,.08,4.2,.08,brass);}
 // Layered aerial haze adds distance and prevents toy-like hard skyline silhouettes.
 scene.fog=new THREE.FogExp2('#8cabb7',0.00155);
 const horizonMat=new THREE.MeshBasicMaterial({color:'#8ba7b2',transparent:true,opacity:.28,depthWrite:false,side:THREE.DoubleSide});
 materials.push(horizonMat);
 const hazeGeo=new THREE.CylinderGeometry(625,625,155,96,1,true);geometries.push(hazeGeo);
 const haze=new THREE.Mesh(hazeGeo,horizonMat);haze.position.set(0,-56,0);scene.add(haze);
 // Sky gradient and atmospheric city, authored procedural geometry, no proprietary assets.
 const sky=canvasTex(16,512,(c,w,h)=>{const g=c.createLinearGradient(0,0,0,h);g.addColorStop(0,'#486d87');g.addColorStop(.42,'#99adb5');g.addColorStop(.68,'#e2be99');g.addColorStop(1,'#e7bf9d');c.fillStyle=g;c.fillRect(0,0,w,h)});
 scene.background=sky;scene.fog=new THREE.FogExp2('#c6b4a2',.0016);
 // A high-altitude photograph replaces the close, block-shaped city geometry.
 // Christian David / Wikimedia Commons, CC BY-SA 4.0. Attribution is in settings.
 const photo=new THREE.TextureLoader().load('/skyline.jpg',t=>{t.colorSpace=THREE.SRGBColorSpace;t.anisotropy=Math.min(8,renderer.capabilities.getMaxAnisotropy());},undefined,()=>ui.hint('Het stadsuitzicht kon niet laden.'));
 textures.push(photo);
 const cityPhoto=new THREE.MeshBasicMaterial({map:photo,fog:false,side:THREE.DoubleSide});materials.push(cityPhoto);
 const cityBackdrop=new THREE.Group();scene.add(cityBackdrop);
 for(const [x,z,rotation] of [[0,-180,0],[180,0,-Math.PI/2],[0,180,Math.PI],[-180,0,Math.PI/2]]){
  const geometry=new THREE.PlaneGeometry(580,387);geometries.push(geometry);
  const image=new THREE.Mesh(geometry,cityPhoto);image.position.set(x,-45,z);image.rotation.y=rotation;cityBackdrop.add(image);
 }
 const hemi=new THREE.HemisphereLight('#dce7f3','#72604b',2);scene.add(hemi);
 const sun=new THREE.DirectionalLight('#ffcf99',3.8);sun.position.set(-45,37,22);sun.castShadow=true;sun.shadow.mapSize.set(2048,2048);Object.assign(sun.shadow.camera,{left:-28,right:28,top:25,bottom:-25,near:.5,far:150});sun.shadow.bias=-.00015;sun.shadow.normalBias=.045;scene.add(sun);
 const fill=new THREE.DirectionalLight('#bbd9ed',1.05);fill.position.set(25,15,-10);scene.add(fill);
 // An inexpensive prefiltered environment creates proper material highlights.
 const environmentScene=new THREE.Scene();environmentScene.background=new THREE.Color('#aab7c0');const envLight=new THREE.Mesh(new THREE.BoxGeometry(40,15,1),new THREE.MeshBasicMaterial({color:'#fff0d8'}));envLight.position.set(-12,12,-10);environmentScene.add(envLight);
 const pmrem=new THREE.PMREMGenerator(renderer),env=pmrem.fromScene(environmentScene,.08);scene.environment=env.texture;pmrem.dispose();envLight.geometry.dispose();envLight.material.dispose();
 // Soft, individually sculpted desk accessories and greenery.
 let decor=null;
 function plush(x,z,color='#f0cbb3',scale=.26){
   const fur=material(color,.96),eyes=material('#24242a',.7),blush=material('#d58f8b',.96);
   ball(x,.98,z,.23*scale/.26,.24*scale/.26,.18*scale/.26,fur);
   for(const side of [-1,1]){ball(x+side*.14,.1+1.11,z,.095,.13,.075,fur);ball(x+side*.085,1.02,z+.17,.021,.025,.018,eyes);}
   ball(x, .93,z+.183,.04,.03,.025,blush);
 }
 function plant(x,z,size=1){cyl(x,.3*size,z,.32*size,.6*size,stone);cyl(x,.85*size,z,.025*size,1.2*size,walnut);for(let i=0;i<13;i++){const angle=i*2.4;const leaf=ball(x+Math.cos(angle)*.32*size,(1.05+rand()*.55)*size,z+Math.sin(angle)*.32*size,.18*size,.38*size,.08*size,green);leaf.rotation.set(rand()*.5,angle,.6)}collision(x,z,.7*size,.7*size)}
 for(const [x,z,s] of [[-17,-13,1.4],[17,-13,1.4],[-17,13,1.5],[17,13,1.5],[-4,1,1.1],[4,1,1.1],[-5,-4,1],[5,-4,1]])plant(x,z,s);
 function chair(x,z,angle=0,m=darkLeather){const group=new THREE.Group();group.position.set(x,0,z);group.rotation.y=angle;scene.add(group);box(0,.49,0,.62,.13,.61,m,group);box(0,.94,-.27,.61,.85,.1,m,group);for(const dx of [-.35,.35]){box(dx,.73,0,.06,.07,.55,ink,group);box(dx,.6,.17,.04,.26,.04,ink,group)}cyl(0,.25,0,.045,.42,ink,group);for(let i=0;i<5;i++){const leg=box(0,.08,.15,.045,.04,.36,ink,group);leg.rotation.y=i*Math.PI*2/5;leg.position.x=Math.sin(i*Math.PI*2/5)*.15;leg.position.z=Math.cos(i*Math.PI*2/5)*.15}return group}
 function screenTexture(title){return canvasTex(512,320,c=>{c.fillStyle='#13232e';c.fillRect(0,0,512,320);c.fillStyle='#c7ac7f';c.font='20px sans-serif';c.fillText(title,30,44);c.fillStyle='#273943';c.fillRect(25,70,110,220);for(let i=0;i<8;i++){c.fillStyle=i%3===0?'#bda482':'#7d9ea0';c.fillRect(155,90+i*23,90+rand()*160,4)}c.fillStyle='#9eb7a1';c.fillRect(37,91,73,4)})}
 function desk(x,z,title,id){box(x,.78,z,3.5,.11,1.6,walnut);box(x-1.45,.39,z,.11,.72,1.4,ink);box(x+1.45,.39,z,.11,.72,1.4,ink);collision(x,z,3.5,1.6);
  box(x,.88,z-.23,.5,.035,.35,ink);box(x,1.03,z-.32,.055,.28,.06,ink);const monitor=box(x,1.39,z-.32,1.13,.68,.055,black);
  const m=new THREE.MeshBasicMaterial({map:screenTexture(title)});materials.push(m);const panel=new THREE.Mesh(new THREE.PlaneGeometry(1.05,.6),m);geometries.push(panel.geometry);panel.rotation.y=Math.PI;panel.position.set(x,1.39,z-.35);scene.add(panel);
  if(id){target(monitor,id);target(panel,id)}box(x,.852,z-.01,.85,.025,.25,black);box(x+.58,.86,z,.12,.035,.2,ink);
  cyl(x+1.16,.96,z+.12,.085,.21,white);box(x-1.16,.85,z+.18,.38,.035,.52,lightWood);
 }
 for(const [id,x,name] of [['maya',-10,'MAYA / ZCLOUD'],['noah',0,'NOAH / SUPA'],['lina',10,'LINA / LIGHTUP']]){desk(x,-8,name,null);chair(x,-9.2);person(id,x,-9.2)}
 function person(id,x,z){const g=new THREE.Group();g.position.set(x,0,z);scene.add(g);g.userData.action=id;targets.push(g);
  const skin=material(id==='noah'?'#ae805f':id==='maya'?'#c99679':'#d6b497',.9),suit=material(id==='maya'?'#414e50':id==='noah'?'#344452':'#796c61',.93),hair=material(id==='lina'?'#aa8650':'#302b28',.94);
  // Anatomical seated pose: hips above the seat, thighs forward, knees and shoes grounded.
  ball(0,.76,0,.24,.3,.14,suit,g);ball(0,1.04,0,.245,.30,.15,suit,g);box(0,1.09,.13,.12,.31,.01,white,g);
  for(const dx of [-.125,.125]){const lapel=box(dx,1.12,.145,.12,.27,.015,suit,g);lapel.rotation.z=dx>0?-.3:.3}
  cyl(0,1.34,0,.065,.13,skin,g);ball(0,1.51,0,.145,.19,.135,skin,g);
  const h=ball(0,1.61,-.024,.15,.105,.13,hair,g);h.rotation.x=-.15;
  if(id!=='noah')ball(0,1.48,-.095,.16,.19,.07,hair,g);
  ball(0,1.49,.133,.025,.035,.022,skin,g);
  for(const dx of [-.052,.052]){ball(dx,1.54,.118,.022,.01,.012,white,g);ball(dx,1.54,.129,.008,.009,.004,black,g);box(dx,1.57,.123,.045,.009,.008,hair,g)}
  box(0,1.43,.125,.045,.009,.007,material('#926754',1),g);
  function limb(a,b,r,m){const start=new THREE.Vector3(...a),end=new THREE.Vector3(...b),mid=start.clone().add(end).multiplyScalar(.5),v=end.clone().sub(start);const p=mesh(cylGeo,m,mid.x,mid.y,mid.z,r,v.length(),r,g);p.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),v.normalize());return p}
  for(const side of [-1,1]){limb([side*.26,1.2,0],[side*.3,.87,.12],.069,suit);limb([side*.3,.87,.12],[side*.21,.84,.47],.058,suit);ball(side*.21,.84,.5,.06,.035,.09,skin,g);limb([side*.13,.59,.02],[side*.15,.52,.42],.098,suit);limb([side*.15,.52,.42],[side*.15,.12,.47],.07,suit);ball(side*.15,.08,.54,.083,.065,.15,black,g)}
  const label=canvasTex(512,100,c=>{c.fillStyle='#18272ee8';c.fillRect(0,0,512,100);c.fillStyle='#ead6b4';c.font='32px sans-serif';c.textAlign='center';c.fillText(id==='maya'?'Maya · zCloud':id==='noah'?'Noah · Supa':'Lina · LightUp',256,62)});
  const sm=new THREE.SpriteMaterial({map:label,transparent:true,depthTest:true});materials.push(sm);const badge=new THREE.Sprite(sm);badge.position.set(0,2,0);badge.scale.set(1.7,.33,1);g.add(badge);
  actors.push({id,g,head:h,baseY:g.position.y,working:false});collision(x,z,.7,.75);return g;
 }
 // Cozy customizable corner, intentionally separate from owner workstations.
 desk(12,11,'CREATIVE CORNER',null);chair(12,9.7);
 const decorStart=new Set(scene.children);plush(11.2,10.9,'#f1d3c0');plush(12.2,10.9,'#c5b3df');plant(13.15,10.7,.45);
 const creativeDecor=scene.children.filter(o=>!decorStart.has(o));
 function customize(style){
  creativeDecor.forEach(o=>o.visible=style!=='minimal');
  for(const o of creativeDecor)if(o.material?.color&&o.material!==green&&o.material!==stone&&o.material!==walnut&&o.material.color.getHexString()!=='24242a')o.material.color.set(style==='garden'?'#bbc9a1':'#e4c4dc');
 }
 target(box(12,1.39,10.68,1.14,.69,.06,black),'customize');
  // Executive office: front-facing workstation and low credenza.
 desk(-11,6,'ZCLOUD / YOUR WORKSPACE','computer');chair(-11,4.8);box(-11,.7,12.9,8,1.4,.65,walnut);collision(-11,12.9,8,.65);
 for(let x=-14;x<-7;x+=1.2){box(x,1.46,12.9,.7,.06,.38,ivory)}
 wallLabel('EXECUTIVE OFFICE',-11,3.55,14.8,5).rotation.y=Math.PI;
 // Boardroom with centered, live presentation screen.
 box(11,.76,7,4.9,.14,6.5,walnut);collision(11,7,4.9,6.5);
 box(11,.39,7,2.6,.73,4.5,ink);
 for(const z of [4.9,6.7,8.5]){chair(7.9,z,-Math.PI/2);chair(14.1,z,Math.PI/2)}
 chair(11,11,Math.PI);for(const z of [5,7,9]){box(11,.85,z,.28,.025,.4,ivory);cyl(11.6,.92,z,.065,.18,white)}
 const boardCanvas=document.createElement('canvas');boardCanvas.width=1536;boardCanvas.height=864;const boardTexture=new THREE.CanvasTexture(boardCanvas);boardTexture.colorSpace=THREE.SRGBColorSpace;textures.push(boardTexture);
 const boardMat=new THREE.MeshBasicMaterial({map:boardTexture});materials.push(boardMat);
 box(11,2.6,1.9,6.9,4.04,.19,ink);const boardMesh=new THREE.Mesh(new THREE.PlaneGeometry(6.6,3.71),boardMat);geometries.push(boardMesh.geometry);boardMesh.position.set(11,2.6,2.01);scene.add(boardMesh);target(boardMesh,'board');collision(11,1.9,6.9,.2);
 function wrap(c,text,max){const lines=[];for(const paragraph of text.split('\n')){let line='';for(const word of paragraph.split(' ')){const candidate=line?line+' '+word:word;if(c.measureText(candidate).width>max&&line){lines.push(line);line=word}else line=candidate}lines.push(line)}return lines}
 function setSlide(s,index,total){const c=boardCanvas.getContext('2d');c.fillStyle=index%2?'#20353e':'#e7e0d3';c.fillRect(0,0,1536,864);c.fillStyle=index%2?'#cbb088':'#756044';c.font='23px sans-serif';c.fillText('Z C L O U D   /   T H E   B O A R D R O O M',110,120);c.fillStyle=index%2?'#eee5d5':'#23343b';c.font='72px Georgia';const titleLines=wrap(c,s.title,1270).slice(0,2);titleLines.forEach((l,i)=>c.fillText(l,110,270+i*80));c.font='33px sans-serif';wrap(c,s.body,1240).slice(0,7).forEach((l,i)=>c.fillText(l,110,370+(titleLines.length-1)*65+i*45));c.fillStyle='#a48b67';c.font='23px sans-serif';c.fillText(String(index+1).padStart(2,'0')+' / '+total,1300,780);boardTexture.needsUpdate=true}
 setSlide({title:'A higher perspective.',body:'Your company. Above the city.'},0,1);
 // Lobby conversation lounge and reception island.
 function sofa(x,z,angle){const g=new THREE.Group();g.position.set(x,0,z);g.rotation.y=angle;scene.add(g);box(0,.37,0,3,.35,.95,leather,g);box(0,.74,-.42,3,.68,.19,leather,g);for(const dx of [-1.42,1.42])box(dx,.61,0,.18,.49,.95,leather,g);for(const dx of [-.9,0,.9]){box(dx,.6,.02,.84,.16,.73,leather,g);const pillow=box(dx,.84,-.25,.7,.48,.16,ivory,g);pillow.rotation.x=-.1}collision(x,z,angle?1.2:3.1,angle?3.1:1.2)}
 sofa(-2,9.7,0);sofa(2.3,7.2,-Math.PI/2);cyl(-.3,.38,7.1,1.15,.08,stone);cyl(-.3,.19,7.1,.48,.37,brass);collision(-.3,7.1,2.3,2.3);box(-.4,.44,7,.38,.025,.5,walnut);cyl(.05,.55,7.2,.09,.23,white);
 // Pendant fixtures and warm table lighting.
 for(const [x,z] of [[-.3,7.1],[11,7],[-11,6]]){cyl(x,4.1,z,.012,1.5,brass);const ringGeo=new THREE.TorusGeometry(1.05,.025,8,60);geometries.push(ringGeo);const ring=new THREE.Mesh(ringGeo,glow);ring.rotation.x=Math.PI/2;ring.position.set(x,3.4,z);scene.add(ring);const light=new THREE.PointLight('#ffe1af',8,7,2);light.position.set(x,3.3,z);scene.add(light)}
 // Sculptural object on a pedestal and bookshelves.
 cyl(3.7,1.2,1.7,.3,2.4,stone);const knotGeo=new THREE.TorusKnotGeometry(.31,.07,70,10);geometries.push(knotGeo);const sculpture=new THREE.Mesh(knotGeo,brass);sculpture.position.set(3.7,2.72,1.7);scene.add(sculpture);
 for(const x of [-17.6,17.6]){box(x,1.35,-1,1,2.7,4,walnut);collision(x,-1,1,4);for(let i=0;i<3;i++)box(x,1+i*.65,-1,1.05,.04,4.1,brass);for(let i=0;i<16;i++)box(x+(x<0?.53:-.53),.7+Math.floor(i/8)*.7,-2.6+(i%8)*.42,.15,.45,.23,i%3===0?ivory:lightWood)}
 let yaw=.65,pitch=-.015,last=performance.now(),drag=null,day=false,presenting=false,lastHint='';
 const rooms={creative:{p:[14.7,1.68,9.4],look:[12,1.2,11],label:'CREATIEF BUREAU',title:'Jouw eigen plek.',desc:'Planten, plushjes of een rustig bureau.'},lounge:{p:[3.5,1.68,12.6],look:[-3,1.7,1],label:'THE LOUNGE',title:'A higher perspective.',desc:'Jouw team, jouw projecten, jouw verdieping.'},team:{p:[-5.7,1.68,-5.2],look:[-10,1.5,-9.2],label:'THE WORKFLOOR',title:'Good things take a team.',desc:'Maya, Noah en Lina. Geef je volgende idee een plek.'},office:{p:[-8.3,1.68,4.3],look:[-11,1.45,6],label:'EXECUTIVE OFFICE',title:'Make yourself at home.',desc:'Je eigen kantoor. Een echte verbinding met je VPS.'},boardroom:{p:[11,1.68,12.5],look:[11,2.5,2],label:'THE BOARDROOM',title:'Ideas deserve a stage.',desc:'Van een eerste slide naar je volgende grote plan.'}};
 function setLook(p,look){camera.position.set(...p);const d=new THREE.Vector3(...look).sub(camera.position);yaw=Math.atan2(-d.x,-d.z);pitch=Math.atan2(d.y,Math.hypot(d.x,d.z));camera.rotation.set(pitch,yaw,0,'YXZ')}
 function go(name){if(name==='assistant'){return selectActor('maya')}const r=rooms[name];if(!r)return;setLook(r.p,r.look);keys.clear();ui.room(r.label,r.title,r.desc)}
 go('lounge');
 function selectActor(id){const a=actors.find(a=>a.id===id);if(a){setLook([a.g.position.x+1.9,1.60,a.g.position.z+.1],[a.g.position.x,1.50,a.g.position.z]);ui.room('JE TEAM',id==='maya'?'Maya · je assistent':id==='noah'?'Noah · ontwikkelaar':'Lina · kwaliteitscontrole','Geef werk en volg het echte resultaat.');}}
 const monitorCanvas=document.createElement('canvas');monitorCanvas.width=1024;monitorCanvas.height=640;
 const monitorTexture=new THREE.CanvasTexture(monitorCanvas);monitorTexture.colorSpace=THREE.SRGBColorSpace;textures.push(monitorTexture);
 const monitorMaterial=new THREE.MeshBasicMaterial({map:monitorTexture});materials.push(monitorMaterial);
 const monitorGeometry=new THREE.PlaneGeometry(3.2,2);geometries.push(monitorGeometry);
 const monitorWall=new THREE.Mesh(monitorGeometry,monitorMaterial);monitorWall.position.set(-.5,2.35,.99);monitorWall.rotation.y=Math.PI;scene.add(monitorWall);target(monitorWall,'monitor');
 function setMonitor(jobs,online){const c=monitorCanvas.getContext('2d');c.fillStyle='#132329';c.fillRect(0,0,1024,640);c.fillStyle='#e8decd';c.font='46px sans-serif';c.fillText('Je team aan het werk',64,90);c.font='22px sans-serif';c.fillStyle='#b4c6be';c.fillText(online?'Werkqueue bereikbaar':'Werkqueue niet bevestigd',64,135);
  for(let i=0;i<3;i++){const id=['maya','noah','lina'][i],all=jobs.filter(j=>j.employee===id),busy=all.some(j=>['claimed','running','verifying'].includes(j.status));c.fillStyle='#22383c';c.fillRect(60,180+i*130,904,106);c.fillStyle=busy?'#aad3b8':'#d3b991';c.beginPath();c.arc(91,220+i*130,7,0,Math.PI*2);c.fill();c.fillStyle='#ede4d5';c.font='28px sans-serif';c.fillText(['Maya · zCloud','Noah · Supa','Lina · LightUp'][i],115,229+i*130);c.font='21px sans-serif';c.fillStyle='#b5c4be';c.fillText(busy?'Werkt aan een opdracht':all.some(j=>['queued','dispatching','dispatched'].includes(j.status))?'Werk staat klaar':'Geen actieve opdracht bevestigd',115,264+i*130);}
  monitorTexture.needsUpdate=true;
 }
 setMonitor([],false);
 const ray=new THREE.Raycaster(),mouse=new THREE.Vector2(),v=new THREE.Vector3();
 function hitAt(x=0,y=0){mouse.set(x,y);ray.setFromCamera(mouse,camera);const hits=ray.intersectObjects(targets,true);for(const h of hits){if(h.distance>18)continue;let o=h.object;while(o&&!o.userData.action)o=o.parent;if(o)return o.userData.action}return ''}
 on(canvas,'pointerdown',e=>{if(e.button!==0)return;drag={x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY,moved:false};canvas.setPointerCapture(e.pointerId)});
 on(canvas,'pointermove',e=>{if(document.pointerLockElement===canvas||drag){const dx=document.pointerLockElement===canvas?e.movementX:e.clientX-drag.x,dy=document.pointerLockElement===canvas?e.movementY:e.clientY-drag.y;yaw-=dx*.003;pitch=THREE.MathUtils.clamp(pitch-dy*.0025,-1.05,1.1);if(drag){drag.moved ||= Math.hypot(e.clientX-drag.startX,e.clientY-drag.startY)>5;drag.x=e.clientX;drag.y=e.clientY}}});
 on(canvas,'pointerup',e=>{if(drag&&!drag.moved){const r=canvas.getBoundingClientRect(),id=hitAt((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);if(id)ui.interact(id)}drag=null});
 on(canvas,'pointercancel',()=>drag=null);
 on(document,'pointerlockchange',()=>ui.locked(document.pointerLockElement===canvas));
 const editable=()=>/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName);
 on(document,'keydown',e=>{if(editable()||ui.isUIOpen())return;const key=e.key.toLowerCase();if(['w','a','s','d','arrowleft','arrowright','arrowup','arrowdown','shift','e'].includes(key)){e.preventDefault();keys.add(key);if(key==='e'&&!e.repeat){const id=hitAt();if(id)ui.interact(id)}}});
 on(document,'keyup',e=>keys.delete(e.key.toLowerCase()));on(window,'blur',()=>keys.clear());
 const canMove=(x,z)=>Math.abs(x)<18.5&&Math.abs(z)<14.5&&!colliders.some(c=>Math.abs(x-c.x)<c.w&&Math.abs(z-c.z)<c.d);
 function resize(){camera.aspect=ui.mount.clientWidth/ui.mount.clientHeight;camera.updateProjectionMatrix();renderer.setSize(ui.mount.clientWidth,ui.mount.clientHeight,false)}
 const observer=new ResizeObserver(resize);observer.observe(ui.mount);resize();
 const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
 renderer.setAnimationLoop(t=>{const dt=Math.min(.035,Math.max(0,(t-last)/1000));last=t;
  if(document.hidden)return;
  if(!ui.isUIOpen()){const f=(keys.has('w')||keys.has('arrowup')?1:0)-(keys.has('s')||keys.has('arrowdown')?1:0),s=(keys.has('d')?1:0)-(keys.has('a')?1:0),norm=Math.hypot(f,s)||1,pace=(keys.has('shift')?5.8:3)*dt;const dx=(-Math.sin(yaw)*f+Math.cos(yaw)*s)/norm*pace,dz=(-Math.cos(yaw)*f-Math.sin(yaw)*s)/norm*pace;if(canMove(camera.position.x+dx,camera.position.z))camera.position.x+=dx;if(canMove(camera.position.x,camera.position.z+dz))camera.position.z+=dz;if(keys.has('arrowleft'))yaw+=dt;if(keys.has('arrowright'))yaw-=dt}
  camera.rotation.set(pitch,yaw,0,'YXZ');
  if(!reduced)for(const a of actors){a.head.rotation.z=Math.sin(t*.0007+a.g.position.x)*.015}
  const id=ui.isUIOpen()?'':hitAt();const hint=id?(id==='computer'?'E · Gebruik je VPS-computer':id==='board'?'E · Open presentaties':'E · Praat met '+id[0].toUpperCase()+id.slice(1)):'';if(hint!==lastHint){ui.hint(hint);lastHint=hint}
  renderer.render(scene,camera)
 });
 return {go,setSlide,customize,setMonitor,clearKeys:()=>keys.clear(),key:(k,pressed)=>pressed?keys.add(k):keys.delete(k),select:selectActor,setJobs:jobs=>{for(const a of actors)a.working=jobs.some(j=>j.employee===a.id&&['claimed','running','verifying'].includes(j.status))},present:value=>{presenting=value},toggleDay:()=>{day=!day;sun.color.set(day?'#fff2df':'#ffcf99');sun.intensity=day?3:3.8;hemi.intensity=day?2.5:2;renderer.toneMappingExposure=day?1.2:1.15;return day},quality:q=>{renderer.setPixelRatio(Math.min(devicePixelRatio,q==='high'?1.7:1));renderer.shadowMap.enabled=q==='high';resize()},lock:()=>{try{const p=canvas.requestPointerLock();p?.catch(()=>{})}catch{}},dispose:()=>{renderer.setAnimationLoop(null);observer.disconnect();listeners.forEach(f=>f());scene.traverse(o=>{if(o.isMesh&&o.geometry&&!geometries.includes(o.geometry))o.geometry.dispose()});geometries.forEach(g=>g.dispose());materials.forEach(m=>m.dispose());textures.forEach(t=>t.dispose());env.dispose();renderer.dispose()}};
}
