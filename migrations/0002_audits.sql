create table if not exists audits (
  id text primary key,
  url text not null,
  score integer not null,
  status text not null,
  created_at timestamptz not null default now(),
  results text not null,
  recommendations text not null
);

create index if not exists audits_created_at_idx on audits (created_at desc);
