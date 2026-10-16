-- The answer API accepts mode 'auto' (the model decides whether to search); stored sessions and messages must too.
ALTER TABLE chat.sessions DROP CONSTRAINT sessions_mode_check;
ALTER TABLE chat.sessions ADD CONSTRAINT sessions_mode_check CHECK (mode IN ('gk', 'rag', 'both', 'auto'));
ALTER TABLE chat.messages DROP CONSTRAINT messages_mode_check;
ALTER TABLE chat.messages ADD CONSTRAINT messages_mode_check CHECK (mode IN ('gk', 'rag', 'both', 'auto'));
