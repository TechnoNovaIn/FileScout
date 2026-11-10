import React from 'react';

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('FileScout Unhandled Render Error:', error, errorInfo);
    this.setState({ errorInfo });
  }

  handleReload = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          minHeight: '100vh',
          backgroundColor: '#0d1117',
          color: '#e6edf3',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '24px',
          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
        }}>
          <div style={{
            maxWidth: '560px',
            width: '100%',
            backgroundColor: '#161b22',
            border: '1px solid #30363d',
            borderRadius: '10px',
            padding: '28px',
            boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
            textAlign: 'center'
          }}>
            <div style={{ fontSize: '36px', marginBottom: '12px' }}>◈</div>
            <h2 style={{ margin: '0 0 8px 0', fontSize: '20px', fontWeight: 600, color: '#f85149' }}>
              Something went wrong while displaying results
            </h2>
            <p style={{ margin: '0 0 20px 0', fontSize: '14px', color: '#8b949e', lineHeight: 1.5 }}>
              An error occurred during rendering. The application caught it to prevent a blank screen.
            </p>
            {this.state.error && (
              <pre style={{
                textAlign: 'left',
                backgroundColor: '#0d1117',
                border: '1px solid #21262d',
                borderRadius: '6px',
                padding: '12px',
                fontSize: '12px',
                color: '#ff7b72',
                overflowX: 'auto',
                marginBottom: '20px',
                maxHeight: '150px'
              }}>
                {this.state.error.toString()}
              </pre>
            )}
            <button
              onClick={this.handleReload}
              style={{
                backgroundColor: '#238636',
                color: '#ffffff',
                border: 'none',
                borderRadius: '6px',
                padding: '8px 20px',
                fontSize: '14px',
                fontWeight: 600,
                cursor: 'pointer',
                transition: 'background-color 0.2s'
              }}
              onMouseOver={(e) => e.target.style.backgroundColor = '#2ea043'}
              onMouseOut={(e) => e.target.style.backgroundColor = '#238636'}
            >
              Refresh Application
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
