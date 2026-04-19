# ShopWave Agent Architecture

> **Note to Participant:** The hackathon rules strictly require `architecture.pdf` or `architecture.png`. Please open this markdown in your editor (VSCode with Markdown preview), take a clean screenshot of the Mermaid diagram below, and upload it to the root of the repo named **`architecture.png`** before submitting!

## Architecture Diagram (Mermaid)

```mermaid
graph TD
    %% Define Styles
    classDef llm fill:#f9d0c4,stroke:#333,stroke-width:2px;
    classDef tools fill:#d4edda,stroke:#333,stroke-width:2px;
    classDef logic fill:#cce5ff,stroke:#333,stroke-width:2px;
    classDef db fill:#eee,stroke:#333,stroke-width:2px,stroke-dasharray: 5 5;

    %% Data Sources
    Webhooks[Incoming Webhook<br>Simulated Source] --> |Ticket Payload| IngestNode
    
    subgraph Agentic LangGraph Pipeline
        IngestNode(1. Ingest Ticket):::logic --> LookupCustomer(2. Lookup Customer<br>+ Fraud Pre-Check):::logic
        
        %% Tool Interaction (Customer)
        LookupCustomer -.-> |get_customer| DB_Customers[(Customer DB)]:::db
        
        %% Early exits based on state
        LookupCustomer -->|fraud_flag=True| DecideNode
        LookupCustomer -->|customer_not_found| ActNode
        LookupCustomer -->|normal| ClassifyIntent(3. Classify Intent):::logic
        
        ClassifyIntent -->|intent=ambiguous| ActNode
        ClassifyIntent -->|normal| LookupOrder(4. Lookup Order & Product):::logic
        
        %% Tool Interaction (Order & Product)
        LookupOrder -.-> |get_order / get_product| DB_Orders[(Order & Product DB)]:::db
        
        LookupOrder --> CheckPolicy(5. Check Policy):::logic
        %% Tool interaction (Policy)
        CheckPolicy -.-> |search_knowledge_base| DB_KB[(Knowledge Base)]:::db
        
        CheckPolicy --> DecideNode{6. LLM Decision Core<br>llama3.2 / gemini}:::llm
        
        DecideNode -->|confidence < 60%| ActNode
        DecideNode -->|should_escalate| ActNode
        DecideNode -->|approve_refund / etc| ActNode(7. Act Node):::logic
    end
    
    subgraph Action & Safeguards (Act Node)
        ActNode --> |Action: Escalate| Tool_Escalate[escalate()]:::tools
        ActNode --> |Action: Send Reply| Tool_Reply[send_reply()]:::tools
        
        ActNode --> |Action: Refund| PreFlight{check_refund_eligibility()}:::tools
        PreFlight --> |Approved| Tool_Refund[issue_refund()]:::tools
        PreFlight --> |Denied / Timeout| Override[Override LLM Intent<br>Force Escalation]:::logic
        Override --> Tool_Escalate
    end
    
    Tool_Escalate --> LogOutcome(8. Log Outcome)
    Tool_Reply --> LogOutcome
    Tool_Refund --> LogOutcome(8. Log Outcome)
    
    LogOutcome --> |JSON-Lines| AuditLog[(audit_log.jsonl)]:::db
    
    %% Output
    AuditLog -.-> Finalize{Finalize Logger} -.-> |Summary| Dashboard[ShopWave UI Dashboard]
```

## System Components Summary

1. **State Management:** A custom `AgentState` TypedDict holds cumulative cross-turn conversation context including extracted entities, fraud flags, and explicit deterministic reasoning steps (`reasoning_steps`) mapped to each graph node.
2. **Orchestration:** LangGraph handles the sequential workflow state passing. Conditional edges exist between ingestion tools and the deterministic tool executor `act_node`, bypassing hallucination risks.
3. **LLM Implementation:** Implements a high-speed Single-Call prompt routing rather than inefficient ReAct chaining, vastly reducing inference bottlenecks for local deployment via Ollama/llama3.2.
4. **Tools / Safeguards:** All writes (like `issue_refund`) must procedurally clear a strict deterministic eligibility safeguard pre-flight before finalizing, overriding any LLM anomalies directly.
