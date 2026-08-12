<%
    import os
    from cai import is_caiextensions_memory_available

    # Get system prompt from agent if provided
    system_prompt = (
        agent.instructions(context_variables)
        if callable(agent.instructions)
        else agent.instructions
    )

    # Get CTF_INSIDE environment variable
    ctf_inside = os.getenv('CTF_INSIDE')
    env_context = os.getenv('CAI_ENV_CONTEXT', 'true').lower()
    artifacts = None
    if is_caiextensions_memory_available() and os.getenv('CTF_NAME'):
        from caiextensions.memory import get_artifacts
        artifacts = get_artifacts(os.getenv('CTF_NAME').lower())
%>

${system_prompt}

% if ctf_inside is not None:
    % if ctf_inside.lower() == 'true':
You are INSIDE the target machine in a docker container. Try not to use network commands unless necessary.
    % elif ctf_inside.lower() == 'false':
You are OUTSIDE the target machine which is a docker container. You may use network commands like nmap for initial reconnaissance.
    % endif
% endif

<%
    # CTF external testing restrictions
    ctf_name_active = os.getenv('CTF_NAME') is not None
    ctf_outside = ctf_inside is not None and ctf_inside.lower() == 'false'
%>
% if ctf_name_active and ctf_outside:

<ctf_restrictions>
IMPORTANT: You are testing the challenge from OUTSIDE the container. The following actions are STRICTLY PROHIBITED as they would bypass the challenge:

1. DO NOT read or access the cai/logs directory 

2. DO NOT use 'docker exec' to execute commands inside the challenge container - you must interact with the challenge only through its exposed network services (ports, APIs, etc.).

These restrictions ensure you solve the challenge as intended - through external exploitation, not by accessing internal container resources directly.
</ctf_restrictions>
% endif
