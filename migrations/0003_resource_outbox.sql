CREATE FUNCTION enqueue_resource_event() RETURNS TRIGGER AS $$
DECLARE event_name TEXT;
BEGIN
 IF NEW.kind NOT IN ('file','folder','record','task','event','submission','note','workday','form') THEN RETURN NEW; END IF;
 IF TG_OP='INSERT' THEN event_name:=NEW.kind||'.created';
 ELSIF NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL THEN event_name:=NEW.kind||'.deleted';
 ELSIF NEW.kind='task' AND NEW.data->>'status'='completed' AND OLD.data->>'status' IS DISTINCT FROM 'completed' THEN event_name:='task.completed';
 ELSE event_name:=NEW.kind||'.updated'; END IF;
 INSERT INTO jobs(id,owner_id,kind,payload,idempotency_key)
 VALUES(gen_random_uuid(),NEW.owner_id,'workflow.event',jsonb_build_object('source_id',NEW.id,'event',event_name,'version',NEW.version,'actor_id',NEW.owner_id),'resource:'||NEW.id::text||':'||NEW.version::text)
 ON CONFLICT(owner_id,kind,idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING;
 RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER resource_event_outbox AFTER INSERT OR UPDATE ON resources FOR EACH ROW EXECUTE FUNCTION enqueue_resource_event();
