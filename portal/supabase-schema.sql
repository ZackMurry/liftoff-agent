create table webhook_events (
  id            uuid primary key default gen_random_uuid(),
  github_event  text not null,
  action        text,
  delivery_id   text unique,
  payload       jsonb not null,
  created_at    timestamptz not null default now()
);

create table pull_requests (
  id              uuid primary key default gen_random_uuid(),
  github_pr_id    bigint not null,
  owner           text not null,
  repo            text not null,
  title           text not null,
  branch          text not null,
  author          text not null,
  head_sha        text not null,
  status          text not null default 'active',
  recommendation  text not null default 'forming',
  review_body     text,
  diff_length     int,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique(owner, repo, github_pr_id, head_sha)
);

create table experiments (
  id              uuid primary key default gen_random_uuid(),
  pull_request_id uuid not null references pull_requests(id) on delete cascade,
  scenario        text not null,
  params          jsonb,
  status          text not null default 'running',
  verdict         text,
  error           text,
  result          jsonb,
  pass_criteria   jsonb,
  started_at      timestamptz not null default now(),
  finished_at     timestamptz
);

create index idx_pr_status on pull_requests(status);
create index idx_pr_repo on pull_requests(owner, repo);
create index idx_exp_pr on experiments(pull_request_id);
