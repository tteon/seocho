# CLAUDE.md - Instructions for Claude

## Project Context
- Working directory: `/home/ubuntu/lab/seocho`
- Platform: Linux AWS environment
- Date: 2025-08-08

## Development Guidelines

### Core Principles
- Use defensive security practices only
- Never create malicious code or tools
- Prefer editing existing files over creating new ones
- Follow existing code conventions and patterns
- Always verify changes with available tests/linting

### Security Focus
- Create security analysis tools and detection rules
- Build defensive security measures
- Document vulnerabilities and mitigation strategies
- Develop security monitoring capabilities

### File Operations
- Always check if files exist before creating new ones
- Use absolute paths for all file operations
- Validate file contents before making changes
- Never commit secrets or sensitive information

### Testing & Validation
- Run appropriate tests/linting after changes
- Use `npm test`, `npm run lint`, or language-specific tools
- Verify security measures don't break functionality
- Test defensive tools against known attack patterns

### Communication
- Be concise and direct in responses
- Use file paths with line numbers for code references
- Provide actionable security insights
- Focus on defensive security applications only