import { Component, ErrorInfo, ReactNode } from 'react'

interface Props {
  children: ReactNode
  /** Optional label shown in the error card (e.g. page name) */
  name?: string
}

interface State {
  error: Error | null
}

/**
 * Catches render errors in any child subtree and displays a recovery card
 * instead of white-screening the whole app.
 */
export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(`[ErrorBoundary${this.props.name ? ` ${this.props.name}` : ''}]`, error, info.componentStack)
  }

  render(): ReactNode {
    const { error } = this.state
    if (error) {
      return (
        <div
          style={{
            margin: '2rem auto',
            maxWidth: 600,
            padding: '1.5rem',
            border: '1px solid #f87171',
            borderRadius: 8,
            background: '#fef2f2',
            color: '#991b1b',
            fontFamily: 'monospace',
          }}
        >
          <strong style={{ fontSize: '1rem' }}>
            {this.props.name ? `${this.props.name} crashed` : 'Something went wrong'}
          </strong>
          <pre style={{ marginTop: '0.75rem', fontSize: '0.8rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {error.message}
            {'\n\n'}
            {error.stack}
          </pre>
          <button
            onClick={() => this.setState({ error: null })}
            style={{
              marginTop: '1rem',
              padding: '0.4rem 1rem',
              background: '#dc2626',
              color: '#fff',
              border: 'none',
              borderRadius: 4,
              cursor: 'pointer',
            }}
          >
            Retry
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
