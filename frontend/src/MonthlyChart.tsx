import {useLoad} from './hooks'
import {inr} from './api'

// Fixed, CVD-tested categorical order (blue, orange, aqua, gold, magenta, violet);
// "Other" stays a neutral gray so it never reads as a real category.
const PALETTE=['#2a78d6','#eb6834','#1baf7a','#eda100','#e87ba4','#4a3aa7']
const OTHER_COLOR='#9aa0ab'
const MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
const monthLabel=(ym:string)=>{const [y,m]=ym.split('-');return `${MONTHS[parseInt(m,10)-1]} ${y.slice(2)}`}

export default function MonthlyChart(){
  const {data,loading,error}=useLoad<any>('/api/analytics/monthly',{months:[]})
  const months=(data.months||[]).slice(-12)

  const totals:Record<string,number>={}
  months.forEach((m:any)=>Object.entries(m.categories||{}).forEach(([k,v]:any)=>{totals[k]=(totals[k]||0)+v}))
  const topCats=Object.entries(totals).sort((a,b:any)=>b[1]-a[1]).slice(0,6).map(([k])=>k)
  const colorFor=(cat:string)=>{const i=topCats.indexOf(cat);return i>=0?PALETTE[i]:OTHER_COLOR}
  const maxTotal=Math.max(1,...months.map((m:any)=>Object.values(m.categories||{}).reduce((s:number,v:any)=>s+v,0)))

  return <section className="panel">
    <div className="panel-title"><div><h2>Monthly spend by category</h2><p>Last {months.length} months of effective spend (self-transfers and matched refunds excluded)</p></div></div>
    {error&&<div className="error">{error}</div>}
    {loading&&<div className="loading">Loading…</div>}
    <div className="month-chart">
      {months.map((m:any)=>{
        const entries=Object.entries(m.categories||{}) as [string,number][]
        const known=topCats.map(cat=>[cat,entries.find(([k])=>k===cat)?.[1]||0] as [string,number]).filter(([,v])=>v>0)
        const otherTotal=entries.filter(([k])=>!topCats.includes(k)).reduce((s,[,v])=>s+v,0)
        const segs=otherTotal>0?[...known,['Other',otherTotal] as [string,number]]:known
        const total=segs.reduce((s,[,v])=>s+v,0)
        return <div className="month-col" key={m.month} title={`${monthLabel(m.month)}: ${inr(total)}`}>
          <div className="month-stack" style={{height:`${Math.max(2,(total/maxTotal)*100)}%`}}>
            {segs.map(([cat,v])=><i key={cat} style={{height:`${total?(v/total)*100:0}%`,background:colorFor(cat)}}/>)}
          </div>
          <span>{monthLabel(m.month)}</span>
        </div>
      })}
      {months.length===0&&!loading&&<div className="empty compact-empty">Import statements to see a monthly trend.</div>}
    </div>
    <div className="month-legend">
      {topCats.map(cat=><span key={cat}><i style={{background:colorFor(cat)}}/>{cat}</span>)}
      {months.some((m:any)=>Object.keys(m.categories||{}).length>topCats.length)&&<span><i style={{background:OTHER_COLOR}}/>Other</span>}
    </div>
  </section>
}
