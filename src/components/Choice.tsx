import { Children, Fragment, isValidElement, useId } from 'react';
import type { ReactNode } from 'react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';

type Option = { value: string; label: ReactNode; disabled?: boolean };
type ChoiceProps = {
  value: string | number; onChange: (event: { target: { value: string } }) => void;
  children: ReactNode; disabled?: boolean; className?: string; id?: string;
  'aria-label'?: string; required?: boolean; name?: string;
};
function optionsOf(children: ReactNode): Option[] {
  const options: Option[] = [];
  Children.forEach(children, child => {
    if (!isValidElement<{ value?: string | number; children?: ReactNode; disabled?: boolean }>(child)) return;
    if (child.type === Fragment) options.push(...optionsOf(child.props.children));
    else if (child.type === 'option') options.push({ value: String(child.props.value ?? child.props.children ?? ''), label: child.props.children, disabled: child.props.disabled });
  });
  return options;
}
// Preserve the existing controlled value contract, while menus use local Nova components.
export function Choice({ value, onChange, children, disabled, className, id, 'aria-label': ariaLabel, required, name }: ChoiceProps) {
  const generated = useId();
  const empty = `empty-${generated}`;
  return <Select value={String(value) || empty} onValueChange={next => onChange({ target: { value: next === empty ? '' : next } })} disabled={disabled} required={required} name={name}>
    <SelectTrigger id={id || generated} className={className} aria-label={ariaLabel}><SelectValue/></SelectTrigger>
    <SelectContent position="popper" align="start">{optionsOf(children).map(option => <SelectItem key={option.value || empty} value={option.value || empty} disabled={option.disabled}>{option.label}</SelectItem>)}</SelectContent>
  </Select>;
}
