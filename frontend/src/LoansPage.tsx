import {useEffect,useState} from 'react'
import {Landmark,Link2,Plus,Save,Unlink} from 'lucide-react'
import {api,inr} from './api'
import {useLoad} from './hooks'

export default function LoansPage(){
  const loans=useLoad<any[]>('/api/loans',[])
  const [selected,setSelected]=useState<number|null>(null)
  const [showCreate,setShowCreate]=useState(false)
  useEffect(()=>{if(!selected&&loans.data.length)setSelected(loans.data[0].id)},[loans.data.length])
  return <>
    <div className="page-title"><div><h1>Loans & EMI</h1><p>Track home loans and other borrowing with manual principal / interest splits</p></div><button className="primary standalone" onClick={()=>setShowCreate(v=>!v)}><Plus size={16}/>Add loan</button></div>
    {showCreate&&<CreateLoan onCreated={(id)=>{setShowCreate(false);loans.reload();setSelected(id)}}/>}
    <div className="loan-layout">
      <aside className="loan-list panel">
        <div className="panel-title"><div><h2>Your loans</h2><p>{loans.data.length} configured</p></div></div>
        {loans.data.map((loan:any)=><button key={loan.id} className={`loan-list-item ${selected===loan.id?'active':''}`} onClick={()=>setSelected(loan.id)}>
          <div className="loan-icon"><Landmark size={18}/></div><div><b>{loan.name}</b><span>{loan.lender} · {loan.loan_type.replaceAll('_',' ')}</span><small>{loan.payment_count} linked payments · interest {inr(loan.interest_paid||0)}</small></div>
        </button>)}
        {!loans.data.length&&<div className="empty compact-empty">Add your first loan, for example Union Asha Home Loan.</div>}
      </aside>
      <div>{selected?<LoanDetail loanId={selected} refreshList={loans.reload}/>:<section className="panel"><div className="empty">Select or create a loan.</div></section>}</div>
    </div>
  </>
}

function CreateLoan({onCreated}:{onCreated:(id:number)=>void}){
  const [form,setForm]=useState<any>({name:'',lender_name:'',loan_type:'HOME_LOAN',original_principal:'',annual_interest_rate:'',emi:'',start_date:'',term_months:'',account_ref:'',notes:''})
  const [status,setStatus]=useState('')
  const set=(k:string,v:any)=>setForm((f:any)=>({...f,[k]:v}))
  async function submit(){
    try{
      const body:any={...form}
      for(const k of ['original_principal','annual_interest_rate','emi','term_months']) if(body[k]==='') body[k]=null
      if(body.term_months!==null) body.term_months=Number(body.term_months)
      const r:any=await api('/api/loans',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
      onCreated(r.id)
    }catch(e:any){setStatus(e.message)}
  }
  return <section className="panel loan-create"><div className="panel-title"><div><h2>Add loan</h2><p>Use the exact PhonePe counterparty name initially; aliases can be added from its ledger later.</p></div></div>
    <div className="form-grid loan-form">
      <label><span>Loan name</span><input value={form.name} onChange={e=>set('name',e.target.value)} placeholder="Union Asha Home Loan"/></label>
      <label><span>Lender / PhonePe name</span><input value={form.lender_name} onChange={e=>set('lender_name',e.target.value)} placeholder="UNION ASHA"/></label>
      <label><span>Loan type</span><select value={form.loan_type} onChange={e=>set('loan_type',e.target.value)}><option value="HOME_LOAN">Home loan</option><option value="PERSONAL_LOAN">Personal loan</option><option value="VEHICLE_LOAN">Vehicle loan</option><option value="EDUCATION_LOAN">Education loan</option><option value="OTHER">Other</option></select></label>
      <label><span>Original principal (₹)</span><input type="number" step="0.01" value={form.original_principal} onChange={e=>set('original_principal',e.target.value)}/></label>
      <label><span>Interest rate % p.a.</span><input type="number" step="0.01" value={form.annual_interest_rate} onChange={e=>set('annual_interest_rate',e.target.value)}/></label>
      <label><span>Expected EMI (₹)</span><input type="number" step="0.01" value={form.emi} onChange={e=>set('emi',e.target.value)}/></label>
      <label><span>Start date</span><input type="date" value={form.start_date} onChange={e=>set('start_date',e.target.value)}/></label>
      <label><span>Term months</span><input type="number" value={form.term_months} onChange={e=>set('term_months',e.target.value)}/></label>
      <label><span>Account / loan reference</span><input value={form.account_ref} onChange={e=>set('account_ref',e.target.value)}/></label>
      <label className="form-wide"><span>Notes</span><textarea value={form.notes} onChange={e=>set('notes',e.target.value)}/></label>
    </div>
    <div className="actions left"><button className="primary" onClick={submit}><Save size={16}/>Create loan</button></div>{status&&<div className="error">{status}</div>}
  </section>
}

function LoanDetail({loanId,refreshList}:{loanId:number,refreshList:()=>any}){
  const detail=useLoad<any>(`/api/loans/${loanId}`,{payments:[],monthly:[],candidates:[],aliases:[]})
  const [status,setStatus]=useState('')
  async function autoLink(){try{const r:any=await api(`/api/loans/${loanId}/auto-link`,{method:'POST'});setStatus(`Linked ${r.linked} matching debit transactions.`);detail.reload();refreshList()}catch(e:any){setStatus(e.message)}}
  async function link(txId:number){try{await api(`/api/loans/${loanId}/payments/${txId}`,{method:'POST'});detail.reload();refreshList()}catch(e:any){setStatus(e.message)}}
  async function unlink(txId:number){try{await api(`/api/loans/${loanId}/payments/${txId}`,{method:'DELETE'});detail.reload();refreshList()}catch(e:any){setStatus(e.message)}}
  const maxMonth=Math.max(1,...detail.data.monthly.map((m:any)=>m.payment||0))
  return <div>
    {detail.error&&<div className="error">{detail.error}</div>}{detail.loading&&<div className="loading">Loading loan…</div>}
    <section className="panel">
      <div className="loan-detail-head"><div><h2>{detail.data.name}</h2><p>{detail.data.lender} · {(detail.data.loan_type||'').replaceAll('_',' ')}</p></div><button className="ghost" onClick={autoLink}><Link2 size={15}/>Link all matching debits</button></div>
      <div className="loan-metrics">
        <LoanStat label="Original principal" value={detail.data.original_principal!=null?inr(detail.data.original_principal):'—'}/>
        <LoanStat label="Principal paid" value={inr(detail.data.principal_paid||0)}/>
        <LoanStat label="Interest paid" value={inr(detail.data.interest_paid||0)}/>
        <LoanStat label="Estimated outstanding" value={detail.data.outstanding_estimate!=null?inr(detail.data.outstanding_estimate):'—'}/>
        <LoanStat label="Expected EMI" value={detail.data.emi!=null?inr(detail.data.emi):'—'}/>
        <LoanStat label="Rate" value={detail.data.annual_interest_rate!=null?`${detail.data.annual_interest_rate}%`:'—'}/>
      </div>
      {status&&<div className="status-line">{status}</div>}
    </section>

    <section className="panel">
      <div className="panel-title"><div><h2>Monthly payment split</h2><p>Principal and interest are based only on the allocations you enter.</p></div></div>
      <div className="loan-months">{detail.data.monthly.map((m:any)=><div className="loan-month" key={m.month}><b>{m.month}</b><div className="loan-month-bar"><i className="principal" style={{width:`${(m.principal/maxMonth)*100}%`}}/><i className="interest" style={{width:`${(m.interest/maxMonth)*100}%`}}/><i className="unallocated" style={{width:`${(m.unallocated/maxMonth)*100}%`}}/></div><span>{inr(m.payment)} total · {inr(m.interest)} interest · {inr(m.principal)} principal{m.unallocated>0?` · ${inr(m.unallocated)} unallocated`:''}</span></div>)}</div>
      {!detail.data.monthly.length&&<div className="empty compact-empty">Link matching bank debits to start the monthly loan chart.</div>}
      <div className="loan-legend"><span><i className="principal"/>Principal</span><span><i className="interest"/>Interest</span><span><i className="unallocated"/>Not split yet</span></div>
    </section>

    <section className="panel">
      <div className="panel-title"><div><h2>Linked EMI / loan payments</h2><p>Enter the interest and fees for each statement month; principal is calculated as the remaining amount.</p></div></div>
      <div className="payment-list">{detail.data.payments.map((p:any)=><PaymentEditor key={p.transaction.id} loanId={loanId} payment={p} onSaved={()=>{detail.reload();refreshList()}} onUnlink={()=>unlink(p.transaction.id)}/>)}</div>
      {!detail.data.payments.length&&<div className="empty compact-empty">No linked payments yet.</div>}
    </section>

    {detail.data.candidates.length>0&&<section className="panel">
      <div className="panel-title"><div><h2>Matching unlinked debits</h2><p>Transactions to {detail.data.aliases.join(', ')}</p></div></div>
      <div className="candidate-list">{detail.data.candidates.map((tx:any)=><div className="candidate" key={tx.id}><div><b>{new Date(tx.datetime).toLocaleDateString('en-IN')}</b><span>{tx.counterparty||tx.description}</span></div><strong>{inr(tx.amount)}</strong><button onClick={()=>link(tx.id)}><Link2 size={14}/>Link</button></div>)}</div>
    </section>}
  </div>
}

function PaymentEditor({loanId,payment,onSaved,onUnlink}:{loanId:number,payment:any,onSaved:()=>void,onUnlink:()=>void}){
  const [interest,setInterest]=useState(String(payment.interest||0))
  const [fees,setFees]=useState(String(payment.fees||0))
  const [note,setNote]=useState(payment.note||'')
  const [status,setStatus]=useState('')
  useEffect(()=>{setInterest(String(payment.interest||0));setFees(String(payment.fees||0));setNote(payment.note||'')},[payment.interest,payment.fees,payment.note])
  async function save(){try{const r:any=await api(`/api/loans/${loanId}/payments/${payment.transaction.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({interest,fees,note})});setStatus(`Principal ${inr(r.principal)} saved.`);onSaved()}catch(e:any){setStatus(e.message)}}
  return <div className="payment-editor">
    <div className="payment-date"><b>{new Date(payment.transaction.datetime).toLocaleDateString('en-IN')}</b><span>{payment.transaction.counterparty}</span><strong>{inr(payment.transaction.amount)}</strong></div>
    <label><span>Interest ₹</span><input type="number" step="0.01" value={interest} onChange={e=>setInterest(e.target.value)}/></label>
    <label><span>Fees ₹</span><input type="number" step="0.01" value={fees} onChange={e=>setFees(e.target.value)}/></label>
    <label className="payment-note"><span>Note</span><input value={note} onChange={e=>setNote(e.target.value)} placeholder="Bank statement interest for this month"/></label>
    <div className="payment-actions"><button className="primary" onClick={save}><Save size={14}/>Save split</button><button className="icon-btn" title="Unlink" onClick={onUnlink}><Unlink size={15}/></button></div>
    {status&&<small className="payment-status">{status}</small>}
  </div>
}

function LoanStat({label,value}:{label:string,value:string}){return <div className="loan-stat"><span>{label}</span><b>{value}</b></div>}
