import { useEffect, useState } from 'react';
import { Check, LoaderCircle } from 'lucide-react';
import './processing-steps.css';

/** Only the local presentation path uses pacing; errors are never replaced by success. */
export async function withPresentationPacing<T>(request: Promise<T>, enabled: boolean): Promise<T> {
  if (!enabled) return request;
  const [result] = await Promise.all([request, new Promise(resolve => setTimeout(resolve, 1800))]);
  return result;
}
export function ProcessingSteps({ steps }: { steps: string[] }) {
  const [step, setStep] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setStep(value => Math.min(value + 1, steps.length - 1)), 600);
    return () => window.clearInterval(timer);
  }, [steps.length]);
  return <div className="processing-steps" role="status" aria-live="polite">
    <div className="processing-heading"><LoaderCircle size={17} className="spinning"/><strong>{steps[step]}</strong><span>处理中</span></div>
    <ol>{steps.map((label, index) => <li key={label} data-state={index < step ? 'done' : index === step ? 'active' : 'pending'}><i>{index < step ? <Check size={12}/> : index + 1}</i><span>{label}</span></li>)}</ol>
    <div className="processing-track"><span style={{ width: `${(step + 1) / (steps.length + 1) * 100}%` }}/></div>
  </div>;
}
