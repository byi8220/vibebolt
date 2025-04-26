# vibebolt
Vibe code some Rust!

Vibebolt is an experimental MCP server which enables you to write and execute Rust code within a docker container. 

NOTE: This is a pretty crude implementation and might melt your quota (and the polar ice caps).

NOTE: Even if sandboxed, `vibebolt` is still running arbitrary code within a docker container inside the host machine. Use with caution. I've noticed other servers seem to be *way* more comfortable letting a server execute commands on the terminal.

## Prerequisites
Python 3 (TODO: Figure out minimum working version)
[`uv`](https://github.com/astral-sh/uv) package manager.

### Installation
TODO: Add setup guide

### Usage
TODO: Add usage guide

## Random Notes

#### Thoughts on vibe coding?
In my experience, vibe coding is highly iterative, and the agent often gets trapped in a loop, even with chain of thought. If I ever continue this project, a more complex agent probably should focus on exploring some techniques for mitigating this. The crudest idea seems to be "stack more agents".

I've also noticed it often struggles to use `vibebolt`'s APIs in an intended manner. I'm pretty new to agent API design and prompt engineering, so I don't have a good handle of what the best practices are.

#### Why Rust specifically?
No reason. Maybe focusing on Python would have been better, but I wanted to see if AI could take advantage of assembly dumps.

#### Should I be running this locally?
Hopefully, the code inside this codebase is small enough that it can be easily audited.