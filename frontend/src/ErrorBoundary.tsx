import React from 'react'

export class ErrorBoundary extends React.Component<React.PropsWithChildren, {error:string}> {
  state={error:''}
  static getDerivedStateFromError(error:unknown){
    return {error:error instanceof Error?error.message:String(error)}
  }
  render(){
    if(this.state.error){
      return <div className="fatal-error"><h1>Something went wrong</h1><p>{this.state.error}</p><button onClick={()=>location.reload()}>Reload app</button></div>
    }
    return this.props.children
  }
}
