-- Adds the ideas.angle column (strategist's editorial interpretation, distinct
-- from the bare `statement`). 001_init.sql already has this column for fresh
-- installs; this migration brings existing databases up to date.
ALTER TABLE ideas ADD COLUMN IF NOT EXISTS angle TEXT;
