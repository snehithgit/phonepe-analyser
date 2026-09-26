import {useEffect,useState} from 'react'
import {ArrowDownLeft,ArrowUpRight,Link2,Save,Tags,UserRound} from 'lucide-react'
import {api,inr} from './api'
import {useLoad} from './hooks'

export default function CounterpartyLedger({name}:{name:string}){
  const endpoint=`/api/counterparties/${encodeURIComponent(name)}/ledger`
  const {data,error,loading,reload}=useLoad<any>(endpoint,{monthly:[],transactions:[],categories:[],aliases:[]})
  const categories=useLoad<any[]>('/api/categories',[])
  const [displayName,setDisplayName]=useState('')
  const [relationship,setRelationship]=useState('GENERAL')
  const [notes,setNotes]=useState('')
  const [alias,setAlias]=useState('')
  const [status,setStatus]=useState('')

  useEffect(()=>{
    setDisplayName(data.profile?.display_name||data.display_name||name)
    setRelationship(data.profile?.relationship_type||'GENERAL')
    setNotes(data.profile?.notes||'')
  },[data.profile?.id,data.display_name,name])

  async function saveProfile(){
    setStatus('')
    try{
      if(data.profile?.id){
        await api(`/api/counterparty-profiles/${data.profile.id}`,{
          method:'PATCH',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({display_name:displayName,relationship_type:relationship,notes})
        })
      }else{
        await api('/api/counterparty-profiles',{
          method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({display_name:displayName||name,primary_alias:name,relationship_type:relationship,notes})
        })
      }
      setStatus('Profile saved.')
      reload()
    }catch(e:any){setStatus(e.message)}
  }

  async function addAlias(){
    if(!alias.trim()||!data.profile?.id)return
    try{
      await api(`/api/counterparty-profiles/${data.profile.id}/aliases`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({alias})
      })
      setAlias('');setStatus('Alias added.');reload()
    }catch(e:any){
      if(String(e.message||'').includes('belongs to another profile')){
        setStatus('That name already belongs to another profile. Use “Merge profile” to combine both ledgers.')
      }else setStatus(e.message)
    }
  }

  async function mergeProfile(){
    if(!alias.trim()||!data.profile?.id)return
    if(!confirm(`Merge the existing profile for “${alias}” into “${data.display_name||name}”? All aliases, transactions and linked loans will appear under one ledger.`))return
    try{
      const r:any=await api(`/api/counterparty-profiles/${data.profile.id}/merge`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({alias})
      })
      setAlias('')
      setStatus(r.merged?'Profiles merged. Debit and credit history are now combined.':'That name is already part of this profile.')
      reload()
    }catch(e:any){setStatus(e.message)}
  }

  async function categorizeAll(){
    if(!data.profile?.id)return
    try{
      const r:any=await api(`/api/counterparty-profiles/${data.profile.id}/categorize`,{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({category_name:'Personal Lending / Interest'})
      })
      setStatus(`${r.updated} transactions classified as Personal Lending / Interest.`)
      setRelationship('PERSONAL_LENDING')
      await api(`/api/counterparty-profiles/${data.profile.id}`,{
        method:'PATCH',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({relationship_type:'PERSONAL_LENDING'})
      })
      reload()
    }catch(e:any){setStatus(e.message)}
  }

  async function setCategory(txId:number,categoryId:number|null){
    try{
      await api(`/api/transactions/${txId}`,{
        method:'PATCH',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({category_id:categoryId})
      })
      reload()
    }catch(e:any){setStatus(e.message)}
  }

  const maxMonth=Math.max(1,...data.monthly.map((m:any)=>Math.max(m.paid||0,m.received||0)))
  return <>
    <div className="page-title">
      <div><h1>{data.display_name||name}</h1><p>Individual ledger · money paid, money received and monthly history</p></div>
      <button className="ghost" onClick={()=>location.hash='/transactions'}>Back to transactions</button>
    </div>
    {error&&<div className="error">{error}</div>}{loading&&<div className="loading">Loading ledger…</div>}
    <div className="metric-grid">
      <LedgerMetric label="Paid to them" value={inr(data.total_paid||0)} tone="red"/>
      <LedgerMetric label="Received from them" value={inr(data.total_received||0)} tone="green"/>
      <LedgerMetric label="Net cashflow" value={inr(data.net_cashflow||0)} tone={(data.net_cashflow||0)>=0?'green':'red'}/>
      <LedgerMetric label={data.lending_mode?'Balance due to you':'Transactions'} value={data.lending_mode?inr(data.balance_due_to_you||0):String(data.transaction_count||0)}/>
    </div>

    <div className="grid-2">
      <section className="panel">
        <div className="panel-title"><div><h2>Monthly ledger</h2><p>Debit vs credit by month</p></div></div>
        <div className="ledger-months">
          {data.monthly.map((m:any)=><div className="ledger-month" key={m.month}>
            <div className="ledger-month-label"><b>{m.month}</b><span>{m.count} tx</span></div>
            <div className="ledger-bars">
              <div><span>Paid</span><i className="paid" style={{width:`${Math.max(2,(m.paid/maxMonth)*100)}%`}}/></div>
              <div><span>Got</span><i className="received" style={{width:`${Math.max(2,(m.received/maxMonth)*100)}%`}}/></div>
            </div>
            <div className="ledger-month-values"><span className="debit">-{inr(m.paid)}</span><span className="credit">+{inr(m.received)}</span></div>
          </div>)}
          {!data.monthly.length&&<div className="empty">No monthly activity yet.</div>}
        </div>
      </section>

      <section className="panel">
        <div className="panel-title"><div><h2>Person / counterparty profile</h2><p>Merge spelling variants and define the relationship</p></div></div>
        <div className="form-grid">
          <label><span>Display name</span><input value={displayName} onChange={e=>setDisplayName(e.target.value)}/></label>
          <label><span>Relationship</span><select value={relationship} onChange={e=>setRelationship(e.target.value)}>
            <option value="GENERAL">General</option><option value="PERSONAL_LENDING">Personal lending / interest</option>
            <option value="FAMILY">Family</option><option value="BUSINESS">Business</option>
            <option value="LENDER">Bank / lender</option><option value="OTHER">Other</option>
          </select></label>
          <label className="form-wide"><span>Notes</span><textarea value={notes} onChange={e=>setNotes(e.target.value)} placeholder="Loan given, agreed interest, relationship notes…"/></label>
        </div>
        <div className="actions left"><button className="primary" onClick={saveProfile}><Save size={16}/>Save profile</button></div>
        {data.profile?.id&&<>
          <div className="alias-list"><b>Aliases</b>{data.aliases.map((a:string)=><span key={a}>{a}</span>)}</div>
          <div className="inline-form"><input value={alias} onChange={e=>setAlias(e.target.value)} placeholder="Add alias or existing profile name"/><button onClick={addAlias}><Link2 size={15}/>Add alias</button><button className="merge-btn" onClick={mergeProfile}>Merge profile</button></div>
          <div className="merge-help">If debit and credit are split across two existing profiles, enter the other profile name above and choose <b>Merge profile</b>.</div>
          <button className="ghost lending-action" onClick={categorizeAll}><Tags size={15}/>Classify all matching transactions as Personal Lending / Interest</button>
        </>}
        {status&&<div className="status-line">{status}</div>}
        {data.lending_mode&&<div className="info-box"><UserRound size={18}/><div><b>Personal lending view</b><span>Balance due is cash paid minus cash received. Interest is not guessed; classify or note it explicitly.</span></div></div>}
      </section>
    </div>

    <section className="panel table-panel">
      <div className="panel-pad"><div className="panel-title"><div><h2>Ledger transactions</h2><p>{data.transaction_count||0} matching transactions</p></div></div></div>
      <table><thead><tr><th>Date</th><th>Description</th><th>Category</th><th>Type</th><th className="num">Amount</th></tr></thead>
      <tbody>{data.transactions.map((x:any)=><tr key={x.id}><td>{new Date(x.datetime).toLocaleDateString('en-IN')}</td><td><b>{x.counterparty||x.description}</b><small>{x.transaction_id}</small></td><td><select className="table-select" value={x.category_id||''} onChange={e=>setCategory(x.id,e.target.value?Number(e.target.value):null)}><option value="">Uncategorized</option>{categories.data.map((c:any)=><option key={c.id} value={c.id}>{c.name}</option>)}</select></td><td><span className={`badge ${x.direction==='CREDIT'?'green':'red'}`}>{x.direction}</span></td><td className={`num ${x.direction==='CREDIT'?'credit':'debit'}`}>{x.direction==='CREDIT'?'+':'-'}{inr(x.amount)}</td></tr>)}</tbody></table>
      <div className="mobile-tx-list">{data.transactions.map((x:any)=><div className="mobile-tx ledger-mobile" key={x.id}><div className={`tx-icon ${x.direction.toLowerCase()}`}>{x.direction==='CREDIT'?<ArrowDownLeft/>:<ArrowUpRight/>}</div><div className="tx-main"><b>{x.counterparty||x.description}</b><span>{new Date(x.datetime).toLocaleDateString('en-IN')} · {x.category}</span></div><strong className={x.direction==='CREDIT'?'credit':'debit'}>{x.direction==='CREDIT'?'+':'-'}{inr(x.amount)}</strong></div>)}</div>
    </section>
  </>
}

function LedgerMetric({label,value,tone}:{label:string,value:string,tone?:string}){
  return <section className={`ledger-metric ${tone||''}`}><span>{label}</span><b>{value}</b></section>
}
