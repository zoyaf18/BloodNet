-- 018: Audit events for authentication and authorization

CREATE TABLE IF NOT EXISTS audit_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Actor (who did this)
    actor_id uuid REFERENCES users(id) ON DELETE SET NULL,
    
    -- Event classification
    event_type varchar(100) NOT NULL,
    -- USER_REGISTERED, EMAIL_VERIFICATION_SENT, EMAIL_VERIFIED, 
    -- PASSWORD_CHANGED, PASSWORD_RESET_REQUESTED, PASSWORD_RESET_COMPLETED,
    -- LOGIN_SUCCESS, LOGIN_FAILURE, LOGOUT,
    -- ORGANIZATION_CREATED, ORGANIZATION_VERIFIED, ORGANIZATION_SUSPENDED,
    -- INVITATION_SENT, INVITATION_ACCEPTED, INVITATION_EXPIRED,
    -- MEMBERSHIP_ACTIVATED, MEMBERSHIP_SUSPENDED, MEMBERSHIP_REVOKED,
    -- MFA_ENABLED, MFA_DISABLED, MFA_RESET
    
    -- Resource being audited
    resource_type varchar(50) NOT NULL,
    -- 'user', 'organization', 'membership', 'invitation'
    resource_id uuid NOT NULL,
    
    -- What changed
    changes jsonb,  -- {before, after} for important fields
    
    -- Outcome
    status varchar(20) NOT NULL DEFAULT 'success'::text,
    CHECK (status IN ('success', 'failure')),
    
    -- Request context
    ip_address inet,
    user_agent varchar(500),
    
    -- Timestamps
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Indices
CREATE INDEX idx_audit_events_actor_id ON audit_events(actor_id);
CREATE INDEX idx_audit_events_resource_type ON audit_events(resource_type);
CREATE INDEX idx_audit_events_resource_id ON audit_events(resource_id);
CREATE INDEX idx_audit_events_event_type ON audit_events(event_type);
CREATE INDEX idx_audit_events_status ON audit_events(status);
CREATE INDEX idx_audit_events_created_at ON audit_events(created_at DESC);

-- Composite: find all events for a resource
CREATE INDEX idx_audit_events_resource ON audit_events(resource_type, resource_id, created_at DESC);

-- Composite: find all events by actor
CREATE INDEX idx_audit_events_actor_time ON audit_events(actor_id, created_at DESC);
