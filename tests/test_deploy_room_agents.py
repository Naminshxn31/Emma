"""The safety shape of the push-deploy script, asserted on its source.

deploy_room_agents.ps1 is PowerShell, so it is not exercised by this pytest
suite the way the Python is -- but the properties that make it a deployment tool
rather than a worm are textual, and a later edit could erase them silently.
These read the script and fail if it drifts toward the dangerous shapes:

  Discovery. A deploy tool that finds its own targets (ping sweep, ARP, a
  1..254 loop over a subnet) is one nobody can audit. This one must act only on
  the hand-written list.

  Self-propagation. The thing installed is the agent, and the agent has no
  deploy capability; deployment lives only here, run by a person. The script
  must not be reachable from app/ (which would let a running Emma trigger it).

  Secrets in the clear. Credentials come from Get-Credential / a PSCredential
  and are never written to the audit log.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "deploy_room_agents.ps1"
SRC = SCRIPT.read_text(encoding="utf-8")


def test_it_acts_only_on_an_explicit_list_never_discovery():
    # It reads a targets file and iterates it.
    assert "TargetsFile" in SRC
    assert re.search(r"ConvertFrom-Json\)\.targets", SRC)
    # No network discovery of any kind.
    lowered = SRC.lower()
    for scan in ("test-connection", "arp -a", "nmap", "1..254", "1..255",
                 "get-netneighbor", "portscan", "find-", "-scan"):
        assert scan not in lowered, f"looks like discovery: {scan}"


def test_a_wildcard_host_cannot_fan_out():
    """A '*' in the list must be refused, not expanded to every machine."""
    assert "Assert-PlainHost" in SRC
    # The line that validates a host before it is used: it allows only hostname
    # and IP characters, so a wildcard cannot slip through and fan out.
    validator = next((l for l in SRC.splitlines()
                      if "-notmatch" in l and "$Name" in l), None)
    assert validator, "the host validator line is gone"
    assert "*" not in validator and "?" not in validator


def test_the_agent_it_installs_cannot_deploy_the_agent():
    """Deployment stays on the management machine. The room agent's own
    catalogue must not gain a 'deploy'/'push'/'install onto another PC'
    capability -- that would be self-propagation."""
    agent = (ROOT / "app" / "room_agent.py").read_text(encoding="utf-8")
    for word in ("deploy_room", "New-PSSession", "Invoke-Command", "Copy-Item -ToSession",
                 "room_control", "make_room_package"):
        assert word not in agent, f"the agent reaches for deployment: {word}"


def test_deployment_is_not_wired_into_emma():
    """Nothing in app/ may invoke the deploy script -- otherwise a running Emma
    could push software to other machines. Same guard as the agent and caller."""
    for path in sorted((ROOT / "app").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "deploy_room_agents" not in text, path


def test_credentials_are_prompted_not_stored_and_never_logged():
    assert "[System.Management.Automation.PSCredential]" in SRC
    assert "Get-Credential" in SRC
    # The credential is never unwrapped to a plaintext password anywhere.
    assert ".Password" not in SRC
    assert "GetNetworkCredential" not in SRC
    # No audit row carries a credential, token or password. Checked on the call
    # sites (the {...} handed to Write-Audit), not the function's comments.
    rows = re.findall(r"Write-Audit @\{([^}]*)\}", SRC)
    assert rows, "no audit rows found -- did the writer move?"
    for body in rows:
        low = body.lower()
        assert "password" not in low and "token" not in low and "credential" not in low, body


def test_every_target_is_logged_with_who_and_what():
    assert "Write-Audit" in SRC
    assert "$env:USERNAME" in SRC          # who
    assert re.search(r"result\s*=", SRC)   # what happened
    # Both the success and the failure paths write an audit row.
    assert SRC.count("Write-Audit @{") >= 3


def test_it_is_reversible_from_here():
    """-Uninstall runs the target's own uninstall.cmd over the same channel."""
    assert "-Uninstall" in SRC or "$Uninstall" in SRC
    assert "uninstall.cmd" in SRC


# --- regression guards for the three defects the red-team confirmed (2026-09-25) ---

def test_the_whole_list_is_validated_before_anything_is_contacted():
    """A target missing 'host'/'room' used to throw mid-loop under StrictMode and
    abort the run -- every later approved machine silently skipped, no audit. The
    list is now validated up front, so a typo stops the run BEFORE ShouldProcess,
    before credentials, before any machine is contacted."""
    assert "has no '$required'" in SRC
    # Before the confirm GATE (the $PSCmdlet.ShouldProcess call, not the
    # SupportsShouldProcess attribute at the top) and before the credential prompt.
    assert SRC.index("has no '$required'") < SRC.index("$PSCmdlet.ShouldProcess"), \
        "validation must run before the confirm/contact step"
    assert SRC.index("has no '$required'") < SRC.index("Get-Credential")


def test_the_local_token_folder_is_cleaned_on_every_path_not_only_on_success():
    """The per-room package holds a plaintext ROOM_AGENT_TOKEN. Its cleanup used
    to sit at the end of the success path, so any throw before it left the token
    folder on the management machine. It is now removed in a finally, guarded."""
    assert "if ($stage -and (Test-Path $stage)) { Remove-Item $stage" in SRC
    # And that is the ONLY place the local stage is removed -- no success-only copy.
    assert SRC.count("Remove-Item $stage") == 1


def test_a_failed_remote_cleanup_is_reported_not_swallowed():
    """The remote staging copy also holds the token. If scrubbing it fails, the
    operator must be told and it must be audited -- not hidden by an empty catch."""
    assert "catch { }" not in SRC, "no cleanup failure may be silently swallowed"
    assert "could not remove remote staging" in SRC
    assert "stage-cleanup" in SRC


def test_install_success_is_verified_on_the_target_not_assumed():
    """rooms.json used to be written on a hardcoded result='ok' while the remote
    install.cmd exit code was ignored -- so a failed install (Python missing, or
    a non-elevated WinRM token hitting install.cmd's self-elevation no-op) was
    reported as success and rooms.json pointed at an agent that never ran. Now
    the post-condition is checked ON THE TARGET before rooms.json is touched."""
    assert "install did not complete" in SRC
    assert "run_room_agent.py')" in SRC
    assert "Get-ScheduledTask -TaskName 'EmmaRoomAgent'" in SRC
    # verification precedes the rooms.json write
    assert SRC.index("install did not complete") < SRC.index("Write-Utf8NoBom $RoomsFile")
    # and the remote installers are run headless-safe
    assert "ROOM_AGENT_SILENT" in SRC


def test_the_audit_log_redacts_token_shapes_from_error_messages():
    """A corrupt rooms.json parse error can put a token fragment into an
    exception message, and the catch writes that message to the audit log --
    whose contract is 'never a token'. The writer redacts token shapes and
    truncates before writing, so no caller can leak one by accident."""
    assert "Protect-AuditText" in SRC
    assert "ROOM_AGENT_TOKEN=<redacted>" in SRC
    writer = SRC[SRC.index("function Write-Audit"):SRC.index("function Assert-PlainHost")]
    assert "Protect-AuditText" in writer, "redaction must run inside the audit writer"
