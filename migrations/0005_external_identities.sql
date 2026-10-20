CREATE TABLE external_identities (
 issuer TEXT NOT NULL,
 subject TEXT NOT NULL,
 user_id UUID NOT NULL REFERENCES users(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(issuer,subject),
 UNIQUE(issuer,user_id)
);
