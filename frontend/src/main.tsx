import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import AssistantOverlayApp from './AssistantOverlayApp';
import './styles.css';
import './assistant-overlay.css';

type State = { error: Error | null };

class ErrorBoundary extends React.Component<React.PropsWithChildren, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('React crashed:', error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="crash-screen">
          <h1>Frontend crashed</h1>
          <pre>{String(this.state.error.stack || this.state.error.message)}</pre>
        </div>
      );
    }
    return this.props.children;
  }
}

window.addEventListener('error', (event) => console.error('Window error:', event.error || event.message));
window.addEventListener('unhandledrejection', (event) => console.error('Unhandled rejection:', event.reason));

const params = new URLSearchParams(window.location.search);
const assistantMode =
  window.location.pathname === '/assistant' ||
  window.location.pathname === '/assistant-overlay' ||
  params.get('mode') === 'assistant' ||
  params.get('mode') === 'assistant-overlay';

document.body.classList.toggle('assistant-body', assistantMode);
document.body.classList.toggle('assistant-overlay-body', assistantMode);

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      {assistantMode ? <AssistantOverlayApp /> : <App />}
    </ErrorBoundary>
  </React.StrictMode>,
);
