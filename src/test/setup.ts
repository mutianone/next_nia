import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';
import { clearWorkspaceViews } from '../hooks/useWorkspaceView';
afterEach(() => { cleanup(); clearWorkspaceViews(); });
Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation(query => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })) });
window.scrollTo = vi.fn();
HTMLElement.prototype.scrollIntoView = vi.fn();
HTMLElement.prototype.hasPointerCapture = () => false;
HTMLElement.prototype.setPointerCapture = () => {};
HTMLElement.prototype.releasePointerCapture = () => {};
class Observer { observe() {} unobserve() {} disconnect() {} }
vi.stubGlobal('ResizeObserver', Observer);
