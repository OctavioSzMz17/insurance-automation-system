# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-05-09

### Added
- Initial project scaffolding and structure
- PostgreSQL database schema with enums, users, policies, and claims tables
- MCP servers for claims, policies, and users domains
- AI orchestrator with analyst and auditor agents
- Observability layer with logging, metrics, and tracers (LangSmith, Langfuse)
- Kubernetes and Docker infrastructure templates
- Shared common package with schemas and enums
- Comprehensive AI rules and operational standards

### Planned
- PDF document extraction MCP
- Validation pipeline with self-healing retry logic
- Coverage reasoning and decision logic for agents
- HTTP API endpoints for claim submission and management
- Database migration system (Alembic)
- Unit and integration tests
- CI/CD pipeline (GitHub Actions)
- Agent evaluation and monitoring

## [Unreleased]

### Planned Features
- Multi-agent claim triage automation
- Real-time claim status tracking
- Auditor approval workflow
- Claim escalation to manual review
- PDF report parsing and entity extraction
- Policy coverage matching
- Decision audit trail

---

**Guidelines for future entries:**
- Use ISO 8601 date format (YYYY-MM-DD)
- Group changes by: Added, Changed, Deprecated, Removed, Fixed, Security
- Reference ticket IDs and commit SHAs where applicable
- Keep entries concise and user-focused
