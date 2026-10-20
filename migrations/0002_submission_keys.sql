CREATE UNIQUE INDEX submissions_idempotency ON resources((data->>'form_id'),(data->>'submission_key')) WHERE kind='submission' AND deleted_at IS NULL AND data ? 'submission_key';
