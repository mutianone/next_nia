export type PersonInput = {
  name: string; english_name: string; nationality: string; gender: 'unknown' | 'male' | 'female';
  birth_date: string | null; document_type: 'passport' | 'other'; document_number: string;
  document_expiry: string | null; phone: string; address: string; purpose: string;
  source: string; verification: 'pending' | 'verified'; notes: string;
};
export type StayInput = { entry_date: string; permitted_until: string | null; exit_date: string | null; permit_type: string; permit_number: string; purpose: string; source: string; notes: string };
export type Stay = StayInput & { id: string; created_at: string; updated_at: string };
export type Person = PersonInput & { id: string; code: string; archived: boolean; created_at: string; updated_at: string; stays?: Stay[] };
export type PeopleResponse = { items: Person[]; total: number; page: number; page_size: number; stats: { total: number; verified: number; pending: number; archived: number } };
export type ImportPreview = { rows: PersonInput[]; errors: { row: number; message: string }[]; total: number; valid_count: number };
