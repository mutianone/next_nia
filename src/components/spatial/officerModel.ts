import * as T from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

/** A local, fully volumetric interpretation of the supplied Xiaoyi reference. +Z is the face. */
export function buildOfficerModel() {
  const root = new T.Group(); root.name = 'xiaoyi';
  const geometries = new Set<T.BufferGeometry>(), materials = new Set<T.Material>(), textures = new Set<T.Texture>();
  const material = (color: string, roughness = .65, metalness = 0) => { const m = new T.MeshStandardMaterial({ color, roughness, metalness }); materials.add(m); return m; };
  const skin = material('#f4ae88', .53), earInner = material('#ca8065'), lips = material('#ac594c'), mouth = material('#542e30');
  const shirt = material('#6192de', .8), seam = material('#6088bf'), collar = material('#7ba1e0');
  const navy = material('#192944', .82), pants = material('#20252e', .95), leather = material('#171d26', .35), hair = material('#17191e', .7), hairLight = material('#24272d', .72);
  const ivory = material('#fff9ed', .33), iris = material('#694330', .3), pupil = material('#14161a', .23), silver = material('#c5cedb', .29, .65);
  function mesh(g: T.BufferGeometry, m: T.Material, parent: T.Object3D, x = 0, y = 0, z = 0) {
    geometries.add(g); const o = new T.Mesh(g, m); o.position.set(x, y, z); o.castShadow = true; o.receiveShadow = false; parent.add(o); return o;
  }
  function ellipsoid(parent: T.Object3D, m: T.Material, x: number, y: number, z: number, sx: number, sy: number, sz: number) {
    const o = mesh(new T.SphereGeometry(1, 40, 28), m, parent, x, y, z); o.scale.set(sx, sy, sz); return o;
  }
  function box(parent: T.Object3D, m: T.Material, w: number, h: number, d: number, x: number, y: number, z: number, radius = .025) {
    return mesh(new RoundedBoxGeometry(w, h, d, 3, Math.min(radius, w / 3, h / 3, d / 3)), m, parent, x, y, z);
  }
  function tube(parent: T.Object3D, m: T.Material, points: number[][], radius: number) {
    return mesh(new T.TubeGeometry(new T.CatmullRomCurve3(points.map(p => new T.Vector3(...p as [number, number, number]))), 22, radius, 7, false), m, parent);
  }
  // Smooth elliptical sections give the shirt and trousers tailored, continuous surfaces.
  function section(parent: T.Object3D, m: T.Material, rings: [number, number, number][], x = 0, z = 0) {
    const points = rings.map(([y, width]) => new T.Vector2(width, y));
    const g = new T.LatheGeometry(points, 48);
    const positions = g.getAttribute('position');
    for (let i = 0; i < positions.count; i++) { const y = positions.getY(i); const ring = rings.reduce((a, b) => Math.abs(b[0] - y) < Math.abs(a[0] - y) ? b : a); positions.setZ(i, positions.getZ(i) * ring[2] / Math.max(.001, ring[1])); }
    g.computeVertexNormals(); return mesh(g, m, parent, x, 0, z);
  }
  function patch(parent: T.Object3D, m: T.Material, points: number[][], depth = .018) {
    const s = new T.Shape(); points.forEach(([x, y], i) => i ? s.lineTo(x, y) : s.moveTo(x, y)); s.closePath();
    return mesh(new T.ExtrudeGeometry(s, { depth, bevelEnabled: true, bevelSegments: 2, steps: 1, bevelSize: .007, bevelThickness: .006 }), m, parent);
  }
  const body = new T.Group(); root.add(body);
  // Polished shoes, rubber soles and raised stitching.
  for (const sign of [-1, 1]) {
    const x = sign * .255;
    ellipsoid(body, leather, x, .14, .13, .235, .145, .4);
    box(body, leather, .47, .075, .77, x, .045, .12, .06);
    ellipsoid(body, leather, x, .2, -.05, .19, .2, .24);
    for (let i = 0; i < 4; i++) tube(body, hairLight, [[x - .105, .27 - i * .018, .13 + i * .045], [x, .294 - i * .018, .145 + i * .045], [x + .105, .27 - i * .018, .13 + i * .045]], .009);
    section(body, pants, [[.2,.17,.17],[.28,.2,.18],[.38,.195,.18],[.8,.19,.19],[1.15,.21,.21],[1.7,.245,.25],[2.15,.255,.27],[2.32,.23,.23]], x);
    tube(body, leather, [[x, .35, .177],[x,.95,.195],[x,1.5,.245],[x,2.16,.269]], .006);
    tube(body, pants, [[x-.14,.4,.135],[x,.43,.188],[x+.13,.47,.14]], .009);
  }
  section(body, pants, [[2.02,.41,.245],[2.16,.48,.28],[2.38,.46,.27],[2.42,.4,.24]]);
  section(body, shirt, [[2.34,.4,.23],[2.4,.49,.29],[2.5,.47,.27],[2.8,.46,.27],[3.15,.5,.3],[3.55,.59,.305],[3.77,.64,.28],[3.88,.48,.24],[4.02,.23,.18],[4.06,.19,.16]]);
  // Belt, loops, buckle and a narrow shirt placket.
  section(body, leather, [[2.27,.465,.283],[2.3,.48,.29],[2.43,.48,.29],[2.46,.455,.28]]);
  box(body, silver, .28, .18, .038, 0, 2.36, .302, .014);
  box(body, leather, .205, .12, .025, 0, 2.36, .329, .009);
  box(body, silver, .016, .12, .016, .075, 2.36, .35);
  for (const x of [-.35,.35]) box(body, pants, .045, .21, .025, x, 2.36, .24);
  box(body, collar, .048, 1.25, .018, 0, 3.12, .298);
  for (let i=0;i<5;i++) ellipsoid(body,silver,0,2.59+i*.24,.314,.018,.018,.009);
  for (const sign of [-1, 1]) {
    const x = sign * .31;
    box(body, shirt, .31, .37, .012, x, 3.18, .252);
    tube(body, seam, [[x-.153,3.37,.27],[x-.153,3.02,.27],[x,2.99,.27],[x+.153,3.02,.27],[x+.153,3.37,.27]], .006);
    const flap = patch(body, collar, [[x-.16,3.38],[x+.16,3.38],[x+.15,3.27],[x,3.21],[x-.15,3.27]]); flap.position.z = .274;
    ellipsoid(body,silver,x,3.27,.295,.023,.023,.012);
    // Side seams and small cloth folds.
    tube(body,seam,[[sign*.46,2.53,.13],[sign*.465,2.8,.18],[sign*.5,3.08,.18]],.005);
    for(let i=0;i<3;i++) tube(body,shirt,[[sign*.24,2.46+i*.06,.25],[sign*.36,2.49+i*.06,.25],[sign*.43,2.53+i*.06,.21]],.004);
  }
  mesh(new T.CylinderGeometry(.16,.19,.38,32),skin,body,0,4.035,0);
  const leftCollar=patch(body,collar,[[-.21,4.04],[-.03,3.84],[-.16,3.59],[-.34,3.87]]);leftCollar.position.z=.215;
  const rightCollar=patch(body,collar,[[.21,4.04],[.03,3.84],[.16,3.59],[.34,3.87]]);rightCollar.position.z=.215;
  const tie = patch(body,navy,[[0,3.81],[-.075,3.66],[-.13,2.66],[0,2.52],[.13,2.66],[.075,3.66]]);tie.position.z=.335;
  const knot=box(body,navy,.15,.16,.095,0,3.76,.32);knot.rotation.z=.1;
  box(body,silver,.185,.025,.025,0,3.1,.367);
  for(const sign of [-1,1]) { const emblem=mesh(new T.TorusGeometry(.025,.008,6,12),silver,body,sign*.18,3.84,.246); emblem.scale.y=1.3; }
  function badgeTexture(kind: 'number'|'badge'|'sleeve') {
    const canvas = document.createElement('canvas'); canvas.width=512;canvas.height=kind==='number'?160:512;
    const ctx=canvas.getContext('2d'); if(!ctx) return navy;
    ctx.fillStyle='#17253d';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.strokeStyle='#d2d9df';ctx.fillStyle='#d2d9df';ctx.lineWidth=8;
    if(kind==='number'){ctx.font='52px sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText('037394',256,83);}
    else {
      ctx.beginPath();ctx.moveTo(160,115);ctx.lineTo(352,115);ctx.lineTo(332,300);ctx.quadraticCurveTo(256,395,180,300);ctx.closePath();ctx.stroke();
      ctx.beginPath();for(let i=0;i<10;i++){const angle=-Math.PI/2+i*Math.PI/5,r=i%2?36:83;const x=256+Math.cos(angle)*r,y=222+Math.sin(angle)*r;i?ctx.lineTo(x,y):ctx.moveTo(x,y);}ctx.closePath();ctx.fill();
      if(kind==='badge')for(let i=0;i<4;i++){ctx.beginPath();ctx.moveTo(36,180+i*28);ctx.lineTo(151,190+i*24);ctx.moveTo(361,190+i*24);ctx.lineTo(476,180+i*28);ctx.stroke();}
      else{ctx.font='bold 48px sans-serif';ctx.textAlign='center';ctx.fillText('警 察',256,70);ctx.fillStyle='#d1a55b';ctx.fillRect(225,310,62,35);}
    }
    const texture=new T.CanvasTexture(canvas);texture.colorSpace=T.SRGBColorSpace;textures.add(texture);
    const mat=new T.MeshStandardMaterial({map:texture,roughness:.65});materials.add(mat);return mat;
  }
  box(body,badgeTexture('number'),.30,.105,.026,.32,3.58,.262,.009);
  box(body,badgeTexture('badge'),.31,.15,.025,-.32,3.58,.262,.014);
  const arms: T.Group[]=[];
  for (const sign of [-1,1]) {
    const arm=new T.Group();arm.position.set(sign*.61,3.73,0);body.add(arm);arm.rotation.z=sign*.09;arms.push(arm);
    ellipsoid(arm,shirt,sign*.025,-.2,0,.22,.31,.24);
    section(arm,shirt,[[-1.64,.12,.14],[-1.53,.145,.16],[-1.2,.16,.18],[-.94,.17,.18],[-.72,.18,.19],[-.4,.21,.22],[-.13,.21,.23],[.02,.13,.16]],sign*.02);
    box(arm,collar,.27,.14,.29,sign*.02,-1.57,0);
    for(let i=0;i<3;i++)tube(arm,shirt,[[-.13,-.83-i*.07,.12],[0,-.85-i*.07,.18],[.14,-.8-i*.07,.11]],.004);
    const shoulder=box(arm,navy,.39,.055,.25,0,.045,0,.018);shoulder.rotation.z=sign*.09;
    for(let i=0;i<2;i++){const mark=box(arm,silver,.06,.012,.09,sign*(i*.12-.03),.082,.025,.005);mark.rotation.y=.4;}
    box(arm,silver,.05,.01,.2,sign*.145,.084,0);
    const hand = new T.Group(); hand.position.set(sign*.02,-1.72,0); arm.add(hand);
    ellipsoid(hand,skin,0,-.06,.015,.105,.18,.08);
    for(let i=0;i<4;i++){ const finger=ellipsoid(hand,skin,(i-1.5)*.047,-.22+(i===3?.045:0),.035,.028,.13-(i===3?.025:0),.035);finger.rotation.z=(i-1.5)*-.035; }
    const thumb=ellipsoid(hand,skin,-sign*.10,-.06,.067,.045,.115,.045);thumb.rotation.z=-sign*.35;
    if(sign===1){const sleeve=box(arm,badgeTexture('sleeve'),.2,.28,.018,.204,-.35,.03,.02);sleeve.rotation.y=Math.PI/2;}
  }
  // Jaw/cheeks and a swept hair cap retain the reference's friendly proportions.
  const head = new T.Group();head.name='head';head.position.set(0,4.16,0);body.add(head);
  const headGeometry=new T.SphereGeometry(1,64,48), pos=headGeometry.getAttribute('position');
  for(let i=0;i<pos.count;i++){const y=pos.getY(i);const jaw=y<-.1?1-.18*Math.pow((-y-.1)/.9,.75):1;pos.setXYZ(i,pos.getX(i)*.445*jaw,pos.getY(i)*.565+.47,pos.getZ(i)*.365+(y<-.1?.025:0));}headGeometry.computeVertexNormals();
  const faceMaterial=skin.clone();faceMaterial.vertexColors=true;materials.add(faceMaterial);
  const colors=new Float32Array(pos.count*3),blush=new T.Color('#edb2a3'),color=new T.Color();
  for(let i=0;i<pos.count;i++) { const x=pos.getX(i),y=pos.getY(i),z=pos.getZ(i);const amount=z>0?Math.exp(-((Math.abs(x)-.24)**2/.012+(y-.35)**2/.012))*.6:0;color.set('#ffffff').lerp(blush,amount);colors.set([color.r,color.g,color.b],i*3); }
  headGeometry.setAttribute('color',new T.BufferAttribute(colors,3));mesh(headGeometry,faceMaterial,head);

  for(const sign of [-1,1]){ellipsoid(head,skin,sign*.426,.42,-.005,.097,.155,.076);ellipsoid(head,earInner,sign*.456,.42,.05,.048,.09,.024);}
  // Hair covers the back and crown, with separate swept locks and temple pieces.
  const cap = new T.SphereGeometry(1,48,28,0,Math.PI*2,0,Math.PI*.69);const hp=cap.getAttribute('position');
  for(let i=0;i<hp.count;i++){const x=hp.getX(i),y=hp.getY(i),z=hp.getZ(i);hp.setXYZ(i,x*.459,y*.38+.88,z*.378-.02);if(z>.25&&y<.1)hp.setY(i,.86+Math.abs(x)*.09);}cap.computeVertexNormals();mesh(cap,hair,head);
  for(const sign of [-1,1]){ellipsoid(head,hair,sign*.405,.69,-.06,.065,.24,.22);box(head,hair,.065,.15,.08,sign*.409,.54,.105);}
  for(let i=0;i<7;i++){const x=-.32+i*.10;const lock=ellipsoid(head,hair,x,.98+Math.sin(i/7*Math.PI)*.065,.13,.17,.155,.265);lock.rotation.z=-.60;lock.rotation.x=-.25;}
  const eyes:T.Group[]=[];
  for(const sign of [-1,1]){
    const eye=new T.Group();eye.position.set(sign*.18,.52,.315);eye.rotation.y=sign*.14;head.add(eye);eyes.push(eye);
    ellipsoid(eye,ivory,0,0,0,.117,.086,.049);
    ellipsoid(eye,iris,sign*.008,0,.044,.048,.061,.018);
    ellipsoid(eye,pupil,sign*.008,0,.059,.027,.041,.011);
    ellipsoid(eye,ivory,-.011,.025,.07,.013,.017,.006);
    tube(head,earInner,[[sign*.07,.55,.342],[sign*.16,.609,.35],[sign*.25,.595,.314],[sign*.295,.54,.295]],.012);
    tube(head,hair,[[sign*.075,.72,.33],[sign*.16,.745,.329],[sign*.24,.735,.295],[sign*.30,.70,.27]],.027);
  }
  ellipsoid(head,skin,0,.42,.354,.051,.11,.055);
  ellipsoid(head,skin,0,.337,.397,.065,.052,.052);
  for(const sign of [-1,1])ellipsoid(head,earInner,sign*.039,.309,.416,.012,.008,.006);
  // An inset curved smile, with a single soft lip edge instead of stacked facial primitives.
  const smile=new T.Shape();smile.moveTo(-.17,.245);smile.quadraticCurveTo(0,.19,.17,.245);smile.quadraticCurveTo(.14,.115,0,.115);smile.quadraticCurveTo(-.14,.115,-.17,.245);
  mesh(new T.ShapeGeometry(smile,24),mouth,head,0,0,.359);
  const toothShape=new T.Shape();toothShape.moveTo(-.139,.23);toothShape.quadraticCurveTo(0,.19,.139,.23);toothShape.lineTo(.12,.19);toothShape.quadraticCurveTo(0,.163,-.12,.19);toothShape.closePath();
  mesh(new T.ShapeGeometry(toothShape,24),ivory,head,0,0,.365);
  tube(head,lips,[[-.155,.206,.359],[-.105,.134,.36],[0,.113,.36],[.105,.134,.36],[.155,.206,.359]],.008);
  const baseY=head.position.y;
  return { root, body, head, eyes, arms,
    animate(time: number, enabled: boolean) {
      const t=enabled?time:0;
      body.rotation.z=enabled?Math.sin(t*.9)*.013:0;
      body.scale.y=1+(enabled?Math.sin(t*1.8)*.003:0);
      head.rotation.y=enabled?Math.sin(t*.65)*.09:0;head.rotation.z=enabled?Math.sin(t*.8)*.025:0;head.position.y=baseY;
      arms.forEach((arm,i)=>{arm.rotation.x=enabled?Math.sin(t*1.2+i*.8)*.035:0;});
      const phase=t%4.6,blink=enabled&&phase>.08&&phase<.26?Math.max(.08,Math.abs(phase-.17)/.09):1;eyes.forEach(eye=>{eye.scale.y=blink;});
    },
    dispose(){geometries.forEach(g=>g.dispose());materials.forEach(m=>m.dispose());textures.forEach(t=>t.dispose());},
  };
}
