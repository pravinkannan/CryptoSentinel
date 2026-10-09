# Crypto Sentinel Documentation

Welcome to the Crypto Sentinel documentation. This documentation covers the architecture, mechanics, AI logic, and database schemas of the algorithmic trading engine.

## Table of Contents

1. [01 - System Architecture](01_system_architecture.md): High-level breakdown of the non-blocking execution loop, Dependency Injection, and module organization.
2. [02 - Volatility Life-Cycle & Risk Mechanics](02_volatility_and_risk.md): Detailed explanation of the 3-Phase Volatility Cycle (Impulse Shock, Whipsaw, Resolution) and dynamic liquidation avoidance.
3. [03 - AI Integration & Signal Generation](03_ai_and_signals.md): Explains how Pattern Recognition parses signals, how Pattern Drift is calculated, and how Google Gemini scores the final setup.
4. [04 - Database Schemas](04_database_schemas.md): Details on the Async SQLite (`aiosqlite`) databases, covering paper trading simulation and journal performance metrics.
