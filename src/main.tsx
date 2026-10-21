import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App';
import './styles.css';
import './settings.css';
import './people.css';
import './knowledge.css';
import './assistant.css';
import './alerts.css';
import './documents.css';
import './records.css';
import './ui-tokens.css';
import './ui-upgrade.css';
import { ConfirmationHost } from './components/ConfirmationHost';

ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><App /><ConfirmationHost /></React.StrictMode>);
