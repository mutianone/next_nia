import * as T from 'three';
import { buildOfficerModel } from './officerModel';
import type { SceneOptions, SpatialController } from './types';

/** The reference PNG is only a WebGL fallback; the interactive character is a mesh. */
export function createOfficerScene(host: HTMLElement, initial: SceneOptions, onFailure: () => void): SpatialController {
  const renderer = new T.WebGLRenderer({ alpha: true, antialias: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
  renderer.outputColorSpace = T.SRGBColorSpace;
  renderer.toneMapping = T.ACESFilmicToneMapping;renderer.toneMappingExposure=1.05;
  renderer.shadowMap.enabled=true;renderer.shadowMap.type=T.PCFSoftShadowMap;
  renderer.domElement.setAttribute('aria-hidden','true');host.appendChild(renderer.domElement);
  const scene = new T.Scene(), camera = new T.OrthographicCamera(-3,3,3,-3,.1,30);
  const character = buildOfficerModel();scene.add(character.root);
  const hemisphere=new T.HemisphereLight('#f1f4ff','#686e83',1.7);scene.add(hemisphere);
  const key=new T.DirectionalLight('#fff0e8',2.1);key.position.set(-3,7,6);key.castShadow=true;key.shadow.mapSize.set(1024,1024);key.shadow.camera.left=-3;key.shadow.camera.right=3;key.shadow.camera.top=6;key.shadow.camera.bottom=-1;key.shadow.normalBias=.025;scene.add(key);
  const fill=new T.DirectionalLight('#d5e2ff',.8);fill.position.set(4,4,3);scene.add(fill);
  const rim=new T.DirectionalLight('#e1eeff',1.7);rim.position.set(2,6,-3);scene.add(rim);
  const baseMaterial=new T.MeshStandardMaterial({color:'#263f65',roughness:.45,metalness:.35});
  const ringMaterial=new T.MeshStandardMaterial({color:initial.accent,emissive:initial.accent,emissiveIntensity:.4,roughness:.3,metalness:.4});
  const baseGeometry=new T.CylinderGeometry(.95,1.03,.09,80),base=new T.Mesh(baseGeometry,baseMaterial);base.position.y=-.035;base.receiveShadow=true;scene.add(base);
  const ringGeometry=new T.TorusGeometry(.97,.014,8,96),ring=new T.Mesh(ringGeometry,ringMaterial);ring.rotation.x=-Math.PI/2;ring.position.y=.015;scene.add(ring);
  const shadowMaterial=new T.ShadowMaterial({opacity:.22}),shadowGeometry=new T.PlaneGeometry(5,5),shadow=new T.Mesh(shadowGeometry,shadowMaterial);shadow.rotation.x=-Math.PI/2;shadow.position.y=-.085;shadow.receiveShadow=true;scene.add(shadow);
  let options=initial,disposed=false,visible=true,frame=0,last=0,time=0;
  let yaw=-.12,pitch=0,targetYaw=yaw,targetPitch=0,hoverX=0,hoverY=0,zoom=initial.expanded?1:0;
  let width=1,height=1;
  function cameraFrame(){
    const halfHeight=T.MathUtils.lerp(2.95,1.5,zoom),center=T.MathUtils.lerp(2.7,4.03,zoom),aspect=width/height;
    const fit=Math.max(1, .87/aspect);
    camera.left=-halfHeight*aspect*fit;camera.right=halfHeight*aspect*fit;camera.top=halfHeight*fit;camera.bottom=-halfHeight*fit;
    camera.position.set(0,center+.16,10);camera.lookAt(0,center,0);camera.updateProjectionMatrix();
  }
  function stop(){if(frame)cancelAnimationFrame(frame);frame=0;}
  function fail(){stop();onFailure();}
  function draw(now:number){
    frame=0;if(disposed||!visible||document.hidden)return;
    if(options.animated&&now-last<1000/30){frame=requestAnimationFrame(draw);return;}
    const dt=Math.min((now-last)/1000||0,.05);last=now;if(options.animated)time+=dt;
    const blend=options.animated?1-Math.exp(-dt*10):1;
    yaw+=(targetYaw+hoverX*.20-yaw)*blend;pitch+=(targetPitch+hoverY*.025-pitch)*blend;
    zoom+=((options.expanded?1:0)-zoom)*blend;
    character.root.rotation.y=yaw+(options.animated?Math.sin(time*.55)*.09:0);character.root.rotation.x=pitch;
    character.animate(time,options.animated);cameraFrame();
    try{renderer.render(scene,camera);}catch{fail();return;}
    if(options.animated&&!disposed)frame=requestAnimationFrame(draw);
  }
  function wake(){if(!disposed&&visible&&!document.hidden&&!frame){last=performance.now();frame=requestAnimationFrame(draw);}}
  const resize=()=>{const rect=host.getBoundingClientRect();if(!rect.width||!rect.height)return;width=rect.width;height=rect.height;renderer.setSize(width,height,false);cameraFrame();wake();};
  const observer=new ResizeObserver(resize);observer.observe(host);
  const intersection=new IntersectionObserver(entries=>{visible=entries[0]?.isIntersecting??false;if(visible)wake();else stop();},{rootMargin:'80px'});intersection.observe(host);
  const visibility=()=>document.hidden?stop():wake();document.addEventListener('visibilitychange',visibility);
  const lost=(event:Event)=>{event.preventDefault();fail();};renderer.domElement.addEventListener('webglcontextlost',lost);
  resize();wake();
  return {
    update(next){options=next;ringMaterial.color.set(next.accent);ringMaterial.emissive.set(next.accent);stop();wake();},
    point(x,y){hoverX=options.animated?x:0;hoverY=options.animated?y:0;wake();},
    turn(x,y){hoverX=hoverY=0;targetYaw+=x;targetPitch=T.MathUtils.clamp(targetPitch+y,-.08,.08);wake();},
    reset(){targetYaw=-.12;targetPitch=0;hoverX=hoverY=0;wake();},
    dispose(){disposed=true;stop();observer.disconnect();intersection.disconnect();document.removeEventListener('visibilitychange',visibility);renderer.domElement.removeEventListener('webglcontextlost',lost);character.dispose();[baseGeometry,ringGeometry,shadowGeometry].forEach(g=>g.dispose());[baseMaterial,ringMaterial,shadowMaterial].forEach(m=>m.dispose());renderer.dispose();renderer.forceContextLoss();renderer.domElement.remove();},
  };
}
