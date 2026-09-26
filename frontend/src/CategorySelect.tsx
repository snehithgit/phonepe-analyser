// Single shared category dropdown, used by the Transactions page and the
// Counterparty Ledger page so both stay in sync with one implementation.
export default function CategorySelect({value,categories,onChange,className}:{value:number|null,categories:{id:number,name:string}[],onChange:(id:number|null)=>void,className?:string}){
  return <select className={className||'table-select'} value={value||''} onChange={e=>onChange(e.target.value?Number(e.target.value):null)}>
    <option value="">Uncategorized</option>
    {categories.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}
  </select>
}
