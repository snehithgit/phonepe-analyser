const base = import.meta.env.VITE_API_URL || ''

export async function api<T>(path:string, options:RequestInit = {}):Promise<T>{
  const res = await fetch(`${base}${path}`, options)
  if(!res.ok){
    const text = await res.text()
    let message = text || `HTTP ${res.status}`
    try {
      const body = JSON.parse(text)
      if(body?.detail) message = String(body.detail)
    } catch {
      // Non-JSON error body; keep the plain response text.
    }
    throw new Error(message)
  }
  return res.json()
}

export function inr(value:number){
  return new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:2}).format(value)
}
