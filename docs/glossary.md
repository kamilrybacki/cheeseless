# Glossary: plain terms and code terms

The docs use plain role names. The CLI, MCP tools, proto fields, file names and the
accepted ADRs keep the original code terms, so this table maps one to the other.

| Plain term | Code term | Meaning |
| --- | --- | --- |
| operator | host, operator | The human who runs `cheeseless` and takes the decisions. |
| test writer | `author` | The agent that writes the hidden acceptance tests from the spec. |
| reference coder | `reference` | An agent that writes code from the spec only, never the tests. The tests are cross-checked against its code. |
| coder | `implementer` | The agent that writes the real code against the locked tests. It never sees them. |
| cross-check | `author_crosscheck` | Runs the test writer's tests against the reference coder's code; a red result means a test contradicts the spec. |
| lock the tests, locked tests | `freeze`, frozen | The operator approves the test suite; after that nobody can edit it. |
| test was wrong | dispute `upheld` | The operator agrees a locked test contradicts the spec. |
| test stands | dispute `rejected` | The operator checked the spec and the test stays as it is. |

"Host" also means an ordinary machine (an allowlisted network host, a host path). That use
is unchanged. Accepted ADRs (`docs/adr/`) are immutable and keep the code terms.
