import { Component, lazy, Suspense, useMemo, useState, type ReactNode, type ComponentProps } from 'react';
import type { RelationStudio } from './RelationStudio';
class GraphBoundary extends Component<{children:ReactNode;onRetry:()=>void},{failed:boolean}> {
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true};}
  render(){return this.state.failed ? <div className="p5-empty" role="alert"><p>关系图谱暂未加载成功，请重试。档案和关系记录不受影响。</p><button className="btn btn-secondary" onClick={this.props.onRetry}>重新加载图谱</button></div> : this.props.children;}
}
export function RelationPanel(props:ComponentProps<typeof RelationStudio>){
  const [attempt,setAttempt]=useState(0);
  const Studio=useMemo(()=>lazy(()=>import('./RelationStudio').then(module=>({default:module.RelationStudio}))),[attempt]);
  return <GraphBoundary key={attempt} onRetry={()=>setAttempt(value=>value+1)}><Suspense fallback={<div className="p5-empty">正在载入关系画布…</div>}><Studio {...props}/></Suspense></GraphBoundary>;
}
