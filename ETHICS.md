# Ethics

## Simulator, not companion

Character Tool is a simulator and an instrument. It is built to help someone study fictional character interiority: how a character appraises a situation, what they feel, what they plan, and what they do. It is not a stand-in for a relationship, a source of support, or a companion.

This is not a disclaimer buried at the end. It is a design commitment that shapes specific technical choices described below.

## Transparency as the core safeguard

The inner-state panel, which shows the character's full private appraisal after every exchange, is a deliberate anti-parasocial design choice.

Most AI character tools hide the machinery to make the illusion more seamless. This tool makes the opposite bet. Showing the appraisal, the chosen move, the tension, the pull-back, keeps the seams visible. The goal is legibility, not maximal human-likeness. A more convincing illusion would be easier to build and worse for the user.

The panel is always present. The character's reasoning is not a private backend detail; it is part of what the tool produces and is meant to be read.

## No engagement-maximizing patterns

The tool does not use streaks, notifications, session-length prompts, daily return hooks, or any pattern designed to pull a user back or extend a session. It does not measure or optimize for time spent.

There is no account system. All data stays on the user's machine. The tool does not phone home.

## Memory

The tool does not currently have a persistent memory feature. Conversations are stored as JSON files on disk, and a scene-fact ledger carries established facts forward within a single scene, but there is no cross-scene or cross-conversation memory of the user or the relationship.

A memory framework is marked in the code as a future integration point and is treated as the highest-care area in the design. Persistent memory is what most strongly creates the feeling of a continuous relationship, which is exactly the parasocial pull the rest of the tool tries to keep legible. If and when memory is added, it will need to distinguish clearly between facts the character has observed within a scene and changes to the character's deep patterns, which should remain slow and stable. The resistance design described below must extend into any memory system.

## Resistance as an ethical choice

The resistance governor is both a craft decision and an ethical one.

A character that instantly becomes whatever the user seems to want is the core mechanism of an unhealthy dynamic. The tool makes that hard on purpose. Deep fears, lifelong coping patterns, and self-protective habits are represented in the appraisal prompt as slow-moving and resistant to change within a single scene or conversation. The pull-back step explicitly names what recoil or retreat the character's pattern still produces even when they are drawn forward. The chosen move must honor that pull-back.

This is not there to frustrate users. Realistic resistance is what makes a character a distinct entity rather than a mirror.

## Bias and authorship

The characters are fictional constructs authored by people and run by language models. They carry the assumptions of both.

A persona reflects what its author chose to include, emphasize, or leave out. The underlying model carries biases from its training. The tool makes no claim that its characters are psychologically accurate, neutral, or representative. Any character built with this tool is a product of specific authoring choices layered on a specific model's tendencies.

## Honest limitations

The characters are not real. They do not understand the user. The appraisal output is structured text produced by a language model, not a genuine reading of the user's emotional state.

The tool deliberately keeps its model of the user shallow: it sees only the conversation history and the current message. It does not track the user's emotional state across sessions, does not build a profile of the user, and is not designed to respond to real distress.

This tool is not a source of support, therapy, or companionship. It should not be relied on as a substitute for human connection.
