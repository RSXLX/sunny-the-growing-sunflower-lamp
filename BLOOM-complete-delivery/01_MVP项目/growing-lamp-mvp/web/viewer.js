/* Native WebGL mesh viewer; no CDN, third-party JavaScript or build step.
   STL geometry is the actual downloadable design. Room light is illustrative. */
const I=()=>new Float32Array([1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]);
function mul(a,b){let m=new Float32Array(16);for(let c=0;c<4;c++)for(let r=0;r<4;r++)for(let k=0;k<4;k++)m[c*4+r]+=a[k*4+r]*b[c*4+k];return m;}
function translation(x,y,z){let m=I();m[12]=x;m[13]=y;m[14]=z;return m;}
function rotateX(a){let m=I(),c=Math.cos(a),s=Math.sin(a);m[5]=c;m[6]=s;m[9]=-s;m[10]=c;return m;}
function rotateY(a){let m=I(),c=Math.cos(a),s=Math.sin(a);m[0]=c;m[2]=-s;m[8]=s;m[10]=c;return m;}
function scale(x,y,z){let m=I();m[0]=x;m[5]=y;m[10]=z;return m;}
function norm(v){let d=Math.hypot(...v)||1;return v.map(x=>x/d);}
function cross(a,b){return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];}
function lookAt(eye,target){let z=norm(eye.map((v,i)=>v-target[i])),x=norm(cross([0,1,0],z)),y=cross(z,x);return new Float32Array([x[0],y[0],z[0],0,x[1],y[1],z[1],0,x[2],y[2],z[2],0,-x.reduce((s,v,i)=>s+v*eye[i],0),-y.reduce((s,v,i)=>s+v*eye[i],0),-z.reduce((s,v,i)=>s+v*eye[i],0),1]);}
function perspective(aspect){let f=1/Math.tan(.55/2),near=.1,far=3000;return new Float32Array([f/aspect,0,0,0,0,f,0,0,0,0,(far+near)/(near-far),-1,0,0,2*far*near/(near-far),0]);}
function cylinder(r,h,n=80){let v=[];for(let i=0;i<n;i++){let a=i/n*Math.PI*2,b=(i+1)/n*Math.PI*2,A=[r*Math.cos(a),0,r*Math.sin(a)],B=[r*Math.cos(b),0,r*Math.sin(b)],C=[B[0],h,B[2]],D=[A[0],h,A[2]];v.push(...A,...D,...B,...B,...D,...C,0,0,0,...B,...A,0,h,0,...D,...C);}return v;}
function sphere(r,n=24){let v=[];const p=(a,b)=>[r*Math.sin(a)*Math.cos(b),r*Math.cos(a),r*Math.sin(a)*Math.sin(b)];for(let i=0;i<n;i++)for(let j=0;j<n*2;j++){let a=i/n*Math.PI,b=(i+1)/n*Math.PI,c=j/n*Math.PI,d=(j+1)/n*Math.PI;v.push(...p(a,c),...p(b,c),...p(b,d),...p(a,c),...p(b,d),...p(a,d));}return v;}
function annulus(inner,outer,h,n=128){let v=[];for(let i=0;i<n;i++){let a=i/n*2*Math.PI,b=(i+1)/n*2*Math.PI;let p=(r,t,z)=>[r*Math.cos(t),r*Math.sin(t),z];let A=p(inner,a,h),B=p(outer,a,h),C=p(outer,b,h),D=p(inner,b,h);v.push(...A,...B,...C,...A,...C,...D);A=p(outer,a,0);B=p(outer,b,0);C=p(outer,b,h);D=p(outer,a,h);v.push(...A,...B,...C,...A,...C,...D);}return v;}
function parseSTL(buf){
 let d=new DataView(buf),v=[];
 if(buf.byteLength>=84){let n=d.getUint32(80,true);if(n*50+84===buf.byteLength){if(!n||n>100000)throw Error('STL 面数超出预览限制');for(let i=0;i<n;i++)for(let j=0;j<9;j++)v.push(d.getFloat32(84+i*50+12+j*4,true));}}
 if(!v.length){let txt=new TextDecoder('utf-8',{fatal:true}).decode(buf),re=/vertex\s+([\-+.\deE]+)\s+([\-+.\deE]+)\s+([\-+.\deE]+)/g,m;while((m=re.exec(txt)))v.push(+m[1],+m[2],+m[3]);}
 if(!v.length||v.length%9||v.length>900000||v.some(x=>!Number.isFinite(x)))throw Error('STL 三角网格无效或超限');
 return v;
}
async function loadMesh(url,fit){
 if(url.includes('.glb'))throw Error('原稿尚无可验证预览，请下载 GLB 并转换为静态网格');
 const response=await fetch(url);if(!response.ok)throw Error('模型不可读取，请检查版本权限和文件状态');
 const reader=response.body.getReader(),chunks=[];let bytes=0;
 try{while(true){const {done,value}=await reader.read();if(done)break;bytes+=value.length;if(bytes>12*1024*1024)throw Error('模型超过 12 MiB 预览限制');chunks.push(value);}}
 catch(e){await reader.cancel();throw e;}
 const data=new Uint8Array(bytes);let offset=0;for(const chunk of chunks){data.set(chunk,offset);offset+=chunk.length;}
 let vertices=parseSTL(data.buffer);
 if(fit){const lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];for(let i=0;i<vertices.length;i++){const a=i%3;lo[a]=Math.min(lo[a],vertices[i]);hi[a]=Math.max(hi[a],vertices[i]);}const width=Math.max(...hi.map((v,i)=>v-lo[i]));if(width<1e-12)throw Error('模型尺寸退化');vertices=vertices.map((v,i)=>(v-(lo[i%3]+hi[i%3])/2)*180/width);}
 return vertices;
}
function showMeshError(canvas,error){
 canvas.dataset.error=error.message;canvas.dataset.loaded='false';canvas.title=error.message;canvas.style.visibility='hidden';
 const message=document.createElement('div');message.className='mesh-error';message.setAttribute('role','status');message.textContent='模型预览失败：'+error.message;canvas.parentElement?.append(message);
}
export const themeColors={sunflower:[.86,.69,.33],forest:[.46,.61,.40],stars:[.60,.65,.81]};
export class LampViewer{
 constructor(canvas,{mode='room',theme='sunflower',asset=null,staticView=false,brightness=45,fit=false}={}){
  this.canvas=canvas;this.fit=fit;this.mode=mode;this.theme=theme;this.staticView=staticView;this.meshes=[];this.closed=false;this.yaw=mode==='room'?-.32:-.23;this.pitch=mode==='room'?.12:.22;this.distance=mode==='room'?765:460;this.target=mode==='room'?[0,160,0]:[0,0,0];this.brightness=brightness;
  this.gl=canvas.getContext('webgl',{alpha:true,antialias:true,preserveDrawingBuffer:true});
  if(!this.gl)return new SoftwareLampViewer(canvas,{mode,theme,asset,staticView,brightness,fit});
  const g=this.gl;const vertex=`attribute vec3 position;attribute vec3 normal;uniform mat4 mvp;uniform mat4 model;varying vec3 n;varying vec3 world;void main(){n=normalize(mat3(model)*normal);world=(model*vec4(position,1.)).xyz;gl_Position=mvp*vec4(position,1.);}`;
  const fragment=`precision mediump float;varying vec3 n;varying vec3 world;uniform vec3 color;uniform float glow;void main(){vec3 N=normalize(n);float key=max(dot(N,normalize(vec3(-.5,.9,1.))),0.);float rim=max(dot(N,normalize(vec3(.9,.1,-.3))),0.);float light=.34+.61*key+.12*rim;vec3 c=color*light+color*glow;gl_FragColor=vec4(pow(c,vec3(.86)),1.);}`;
  let shader=(type,src)=>{let s=g.createShader(type);g.shaderSource(s,src);g.compileShader(s);if(!g.getShaderParameter(s,g.COMPILE_STATUS))throw Error(g.getShaderInfoLog(s));return s;};
  this.program=g.createProgram();g.attachShader(this.program,shader(g.VERTEX_SHADER,vertex));g.attachShader(this.program,shader(g.FRAGMENT_SHADER,fragment));g.linkProgram(this.program);g.useProgram(this.program);
  this.locations={};for(let name of ['mvp','model','color','glow'])this.locations[name]=g.getUniformLocation(this.program,name);
  this.pos=g.getAttribLocation(this.program,'position');this.nor=g.getAttribLocation(this.program,'normal');g.enable(g.DEPTH_TEST);g.disable(g.CULL_FACE);
  if(mode==='room')this.addLamp();if(asset)this.load(asset);
  this.observer=new ResizeObserver(()=>this.draw());this.observer.observe(canvas);
  this.onDown=e=>{this.drag={x:e.clientX,y:e.clientY};canvas.setPointerCapture(e.pointerId);};this.onMove=e=>{if(!this.drag)return;this.yaw+=(e.clientX-this.drag.x)*.006;this.pitch=Math.max(-.7,Math.min(.85,this.pitch+(e.clientY-this.drag.y)*.004));this.drag={x:e.clientX,y:e.clientY};this.draw();};this.onUp=()=>this.drag=null;
  canvas.addEventListener('pointerdown',this.onDown);canvas.addEventListener('pointermove',this.onMove);canvas.addEventListener('pointerup',this.onUp);canvas.addEventListener('pointercancel',this.onUp);
  this.onWheel=e=>{if(staticView)return;e.preventDefault();this.distance=Math.max(mode==='room'?500:230,Math.min(1100,this.distance+e.deltaY*.3));this.draw();};canvas.addEventListener('wheel',this.onWheel,{passive:false});this.draw();
 }
 add(vertices,color,matrix=I(),glow=0){let g=this.gl;if(!g||this.closed)return;let normals=[];for(let i=0;i<vertices.length;i+=9){let a=vertices.slice(i,i+3),b=vertices.slice(i+3,i+6),c=vertices.slice(i+6,i+9),n=norm(cross(b.map((v,j)=>v-a[j]),c.map((v,j)=>v-a[j])));normals.push(...n,...n,...n);}let p=g.createBuffer();g.bindBuffer(g.ARRAY_BUFFER,p);g.bufferData(g.ARRAY_BUFFER,new Float32Array(vertices),g.STATIC_DRAW);let n=g.createBuffer();g.bindBuffer(g.ARRAY_BUFFER,n);g.bufferData(g.ARRAY_BUFFER,new Float32Array(normals),g.STATIC_DRAW);this.meshes.push({p,n,count:vertices.length/3,color,matrix,glow});}
 addLamp(){
  this.add(cylinder(67,12),[.29,.30,.24],translation(0,0,0));this.add(cylinder(63,12),[.43,.44,.35],translation(0,12,0));
  this.add(cylinder(6,198),[.46,.47,.35],translation(0,22,-19));
  this.add(cylinder(48,24),[.40,.42,.34],mul(translation(0,235,0),rotateX(-Math.PI/2)));
  this.add(annulus(28,43,3),[.57,.54,.38],translation(0,235,19));
  this.add(cylinder(28,3),[1,.81,.44],mul(translation(0,235,23),rotateX(Math.PI/2)),.2+this.brightness/140);
  this.add(sphere(1),[.38,.49,.31],mul(translation(-18,118,-16),mul(rotateY(-.4),scale(25,9,10))));
  this.add(sphere(1),[.47,.56,.35],mul(translation(17,153,-19),scale(22,8,9)));
 }
 async load(url){try{const vertices=await loadMesh(url,this.fit);if(this.closed)return;this.add(vertices,themeColors[this.theme]||themeColors.sunflower,this.mode==='room'?translation(0,235,15):translation(0,0,-4));this.draw();this.canvas.dataset.loaded='true';}catch(e){if(!this.closed)showMeshError(this.canvas,e);}}
 draw(){let g=this.gl;if(!g||this.closed)return;let rect=this.canvas.getBoundingClientRect();if(!rect.width||!rect.height)return;let dpr=Math.min(devicePixelRatio||1,2);this.canvas.width=Math.round(rect.width*dpr);this.canvas.height=Math.round(rect.height*dpr);g.viewport(0,0,this.canvas.width,this.canvas.height);g.clearColor(0,0,0,0);g.clear(g.COLOR_BUFFER_BIT|g.DEPTH_BUFFER_BIT);g.useProgram(this.program);let t=this.target,eye=[t[0]+Math.sin(this.yaw)*this.distance*Math.cos(this.pitch),t[1]+Math.sin(this.pitch)*this.distance,t[2]+Math.cos(this.yaw)*this.distance*Math.cos(this.pitch)],vp=mul(perspective(rect.width/rect.height),lookAt(eye,t));for(let m of this.meshes){g.uniformMatrix4fv(this.locations.mvp,false,mul(vp,m.matrix));g.uniformMatrix4fv(this.locations.model,false,m.matrix);g.uniform3fv(this.locations.color,m.color);g.uniform1f(this.locations.glow,m.glow);g.bindBuffer(g.ARRAY_BUFFER,m.p);g.enableVertexAttribArray(this.pos);g.vertexAttribPointer(this.pos,3,g.FLOAT,false,0,0);g.bindBuffer(g.ARRAY_BUFFER,m.n);g.enableVertexAttribArray(this.nor);g.vertexAttribPointer(this.nor,3,g.FLOAT,false,0,0);g.drawArrays(g.TRIANGLES,0,m.count);}}
 dispose(){this.closed=true;this.observer?.disconnect();let c=this.canvas;c.removeEventListener('pointerdown',this.onDown);c.removeEventListener('pointermove',this.onMove);c.removeEventListener('pointerup',this.onUp);c.removeEventListener('pointercancel',this.onUp);c.removeEventListener('wheel',this.onWheel);if(this.gl){for(let m of this.meshes){this.gl.deleteBuffer(m.p);this.gl.deleteBuffer(m.n);}this.gl.deleteProgram(this.program);this.gl.getExtension('WEBGL_lose_context')?.loseContext();}}
}

/* Software rasterization fallback: the SAME triangles, not a static mock image.
   Useful on enterprise browsers/VMs without WebGL. No texture/optical simulation. */
class SoftwareLampViewer {
 constructor(canvas,{mode='room',theme='sunflower',asset=null,staticView=false,brightness=45,fit=false}={}) {
  Object.assign(this,{canvas,mode,theme,staticView,brightness,fit,meshes:[],closed:false});
  this.yaw=mode==='room'?-.32:-.23;this.pitch=mode==='room'?.12:.22;
  this.distance=mode==='room'?765:460;this.target=mode==='room'?[0,160,0]:[0,0,0];
  this.ctx=canvas.getContext('2d');canvas.dataset.renderer='canvas-software';canvas.title='软件三维预览 · WebGL 不可用';
  if(mode==='room')LampViewer.prototype.addLamp.call(this);
  if(asset)this.load(asset);
  this.observer=new ResizeObserver(()=>this.draw());this.observer.observe(canvas);
  this.down=e=>{this.drag=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId);};
  this.move=e=>{if(!this.drag)return;this.yaw+=(e.clientX-this.drag[0])*.006;this.pitch=Math.max(-.7,Math.min(.85,this.pitch+(e.clientY-this.drag[1])*.004));this.drag=[e.clientX,e.clientY];this.draw();};
  this.up=()=>this.drag=null;this.wheel=e=>{if(staticView)return;e.preventDefault();this.distance=Math.max(mode==='room'?500:230,Math.min(1100,this.distance+e.deltaY*.3));this.draw();};
  canvas.addEventListener('pointerdown',this.down);canvas.addEventListener('pointermove',this.move);canvas.addEventListener('pointerup',this.up);canvas.addEventListener('pointercancel',this.up);canvas.addEventListener('wheel',this.wheel,{passive:false});
 }
 add(vertices,color,matrix=I(),glow=0){
  const points=[];for(let i=0;i<vertices.length;i+=3){let x=vertices[i],y=vertices[i+1],z=vertices[i+2];points.push([matrix[0]*x+matrix[4]*y+matrix[8]*z+matrix[12],matrix[1]*x+matrix[5]*y+matrix[9]*z+matrix[13],matrix[2]*x+matrix[6]*y+matrix[10]*z+matrix[14]]);}
  const key=norm([-.5,.9,1]),rim=norm([.9,.1,-.3]);const faces=[];
  for(let i=0;i<points.length;i+=3){let a=points[i],b=points[i+1],c=points[i+2],n=norm(cross(b.map((v,j)=>v-a[j]),c.map((v,j)=>v-a[j])));let light=.34+.61*Math.max(0,n.reduce((s,v,j)=>s+v*key[j],0))+.12*Math.max(0,n.reduce((s,v,j)=>s+v*rim[j],0));faces.push({points:[a,b,c],color:'rgb('+color.map(v=>Math.round(Math.min(1,Math.pow(Math.max(0,v*(light+glow)),.86))*255)).join(',')+')'});}
  this.meshes.push(...faces);
 }
 async load(url){try{const vertices=await loadMesh(url,this.fit);if(this.closed)return;this.add(vertices,themeColors[this.theme]||themeColors.sunflower,this.mode==='room'?translation(0,235,15):translation(0,0,-4));this.canvas.dataset.loaded='true';this.draw();}catch(e){if(!this.closed)showMeshError(this.canvas,e);}}
 draw(){
  if(this.closed||!this.ctx)return;const rect=this.canvas.getBoundingClientRect();if(!rect.width||!rect.height)return;
  const dpr=Math.min(devicePixelRatio||1,1.5),w=rect.width*dpr,h=rect.height*dpr;this.canvas.width=w;this.canvas.height=h;
  const eye=[this.target[0]+this.distance*Math.sin(this.yaw)*Math.cos(this.pitch),this.target[1]+this.distance*Math.sin(this.pitch),this.target[2]+this.distance*Math.cos(this.yaw)*Math.cos(this.pitch)];
  const view=lookAt(eye,this.target),matrix=mul(perspective(w/h),view),project=p=>{let x=matrix[0]*p[0]+matrix[4]*p[1]+matrix[8]*p[2]+matrix[12],y=matrix[1]*p[0]+matrix[5]*p[1]+matrix[9]*p[2]+matrix[13],z=matrix[2]*p[0]+matrix[6]*p[1]+matrix[10]*p[2]+matrix[14],q=matrix[3]*p[0]+matrix[7]*p[1]+matrix[11]*p[2]+matrix[15];return[(x/q+1)*w/2,(1-y/q)*h/2,z/q,q];};
  const faces=[];for(const f of this.meshes){let p=f.points.map(project);if(p.some(v=>v[3]<=0))continue;faces.push({p,color:f.color,z:(p[0][2]+p[1][2]+p[2][2])/3});}faces.sort((a,b)=>b.z-a.z);
  const c=this.ctx;c.clearRect(0,0,w,h);c.lineWidth=.45;
  for(const f of faces){c.beginPath();c.moveTo(f.p[0][0],f.p[0][1]);c.lineTo(f.p[1][0],f.p[1][1]);c.lineTo(f.p[2][0],f.p[2][1]);c.closePath();c.fillStyle=f.color;c.strokeStyle=f.color;c.fill();c.stroke();}
 }
 dispose(){this.closed=true;this.observer?.disconnect();this.canvas.removeEventListener('pointerdown',this.down);this.canvas.removeEventListener('pointermove',this.move);this.canvas.removeEventListener('pointerup',this.up);this.canvas.removeEventListener('pointercancel',this.up);this.canvas.removeEventListener('wheel',this.wheel);this.meshes=[];}
}
