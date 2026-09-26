const base = import.meta.env.VITE_API_URL || ''
export async function api<T>(path:string, options:RequestInit = {}):Promise<T>{
  const res = await fetch(`${base}${path}`, options)
  if(!res.ok){throw new Error((await res.text()) || `HTTP ${res.status}`)}
  return res.json()
}
export function inr(value:number){return new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:2}).format(value)}
