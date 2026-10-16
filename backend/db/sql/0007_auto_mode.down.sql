UPDATE chat.sessions SET mode = 'rag' WHERE mode = 'auto';
UPDATE chat.messages SET mode = 'rag' WHERE mode = 'auto';
ALTER TABLE chat.sessions DROP CONSTRAINT sessions_mode_check;
ALTER TABLE chat.sessions ADD CONSTRAINT sessions_mode_check CHECK (mode IN ('gk', 'rag', 'both'));
ALTER TABLE chat.messages DROP CONSTRAINT messages_mode_check;
ALTER TABLE chat.messages ADD CONSTRAINT messages_mode_check CHECK (mode IN ('gk', 'rag', 'both'));
