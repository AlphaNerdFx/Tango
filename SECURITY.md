# Security Policy

## Supported versions

Only the latest release of Tango receives security fixes. If you are on an older version, upgrade before reporting.

| Version | Supported |
|---|---|
| v0.4.x (latest) | Yes |
| v0.3.x and below | No |

## Reporting a vulnerability

Do not open a public GitHub issue for security vulnerabilities. Public issues are visible to everyone immediately and give no time to prepare a fix before the vulnerability is known.

Instead, report vulnerabilities by emailing the maintainer directly. You can find the contact in the GitHub profile associated with this repository. Include the following in your report:

- A description of the vulnerability and what an attacker could do with it
- The version of Tango affected
- Steps to reproduce the issue
- Any suggested fix if you have one

You will receive a response within 48 hours acknowledging the report. If you do not hear back in that time, follow up via a GitHub issue marked as a security concern without including the technical details.

## What counts as a vulnerability

The following are in scope: credential exposure via the .env file or logs, dependency vulnerabilities in packages Tango installs, command injection via user-supplied video IDs or deck names, and any issue that could allow a malicious video transcript or API response to execute code on the user's machine.

The following are out of scope for this version: rate limiting by external APIs, AnkiConnect security (that is Anki's responsibility), and issues requiring physical access to the user's machine.

## Dependency security

Enable the GitHub dependency graph and Dependabot alerts on your fork to be notified when a dependency has a known CVE. Tango uses requests, argostranslate, and youtube-transcript-api which have all had patches in the past. Keeping dependencies up to date is the most effective security measure for this type of tool.