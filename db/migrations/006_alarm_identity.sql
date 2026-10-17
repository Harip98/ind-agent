-- Alarm ids exposed by data-svc are <asset>:<tag>:<alarm_type>:<raised_at>; make that unique.
CREATE UNIQUE INDEX alarms_identity ON ops.alarms (tenant_id, asset_id, tag, alarm_type, raised_at);
