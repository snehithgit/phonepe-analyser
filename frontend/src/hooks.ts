import {useEffect,useRef,useState} from 'react'
import {api} from './api'

export function useDebouncedValue<T>(value:T, delay=250){
  const [debounced,setDebounced]=useState(value)
  useEffect(()=>{
    const timer=window.setTimeout(()=>setDebounced(value),delay)
    return ()=>window.clearTimeout(timer)
  },[value,delay])
  return debounced
}

export function useLoad<T>(path:string, initial:T){
  const [data,setData]=useState<T>(initial)
  const [error,setError]=useState('')
  const [loading,setLoading]=useState(true)
  const requestId=useRef(0)

  const reload=()=>{
    const id=++requestId.current
    const controller=new AbortController()
    setError('')
    setLoading(true)
    api<T>(path,{signal:controller.signal})
      .then(value=>{if(id===requestId.current)setData(value)})
      .catch(err=>{
        if(id===requestId.current && err?.name!=='AbortError') setError(err?.message||String(err))
      })
      .finally(()=>{if(id===requestId.current)setLoading(false)})
    return ()=>controller.abort()
  }

  useEffect(()=>reload(),[path])
  return {data,error,loading,reload}
}
