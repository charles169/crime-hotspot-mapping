import asyncio
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from dotenv import set_key
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine


PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"


async def configure_mysql(host, port_input, database, username, password):
    if not password:
        raise ValueError("Password cannot be empty.")

    try:
        port = int(port_input)
    except ValueError as exc:
        raise ValueError("MySQL port must be a number.") from exc

    database_url = URL.create(
        "mysql+asyncmy",
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
        query={"charset": "utf8mb4"},
    )
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            selected_database = await connection.scalar(text("SELECT DATABASE()"))
            if selected_database != database:
                raise RuntimeError("Connected to an unexpected database.")
    finally:
        await engine.dispose()

    if not ENV_PATH.exists():
        ENV_PATH.touch()
    set_key(
        str(ENV_PATH),
        "CRIME_RADAR_DATABASE_URL",
        database_url.render_as_string(hide_password=False),
        quote_mode="always",
    )
    print(f"Connection verified for database '{database}'.")
    print(f"Configuration saved to {ENV_PATH.name}; credentials were not displayed.")
    print("Restart the FastAPI server to apply the MySQL configuration.")


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Configure C.R.I.M.E. Radar MySQL")
    root.resizable(False, False)
    fields = (
        ("MySQL host", "127.0.0.1"),
        ("Port", "3306"),
        ("Database", "crime_radar"),
        ("Username", "root"),
        ("Password", ""),
    )
    inputs = {}

    for row, (label, default) in enumerate(fields):
        ttk.Label(root, text=label).grid(
            row=row, column=0, padx=(14, 8), pady=6, sticky="w"
        )
        value = tk.StringVar(value=default)
        entry = ttk.Entry(
            root,
            textvariable=value,
            width=38,
            show="*" if label == "Password" else "",
        )
        entry.grid(row=row, column=1, padx=(0, 14), pady=6, sticky="ew")
        inputs[label] = (value, entry)

    password_visible = tk.BooleanVar(value=False)

    def toggle_password():
        inputs["Password"][1].configure(
            show="" if password_visible.get() else "*"
        )

    ttk.Checkbutton(
        root,
        text="Show password",
        variable=password_visible,
        command=toggle_password,
    ).grid(row=len(fields), column=1, padx=14, pady=(0, 8), sticky="w")

    status = ttk.Label(
        root,
        text="Password stays local and is saved only after connection succeeds.",
        wraplength=400,
    )
    status.grid(
        row=len(fields) + 1, column=0, columnspan=2, padx=14, pady=6, sticky="w"
    )

    def test_and_save():
        host = inputs["MySQL host"][0].get().strip()
        port = inputs["Port"][0].get().strip()
        database = inputs["Database"][0].get().strip()
        username = inputs["Username"][0].get().strip()
        password = inputs["Password"][0].get()
        if not password:
            messagebox.showerror("Missing password", "Enter your MySQL password.")
            inputs["Password"][1].focus_set()
            return

        save_button.configure(state="disabled")
        status.configure(text="Testing connection...")
        root.update_idletasks()
        try:
            asyncio.run(
                configure_mysql(
                    host or "127.0.0.1",
                    port or "3306",
                    database or "crime_radar",
                    username or "root",
                    password,
                )
            )
        except Exception as exc:
            messagebox.showerror(
                "MySQL setup failed",
                f"{exc}\n\nCheck your connection settings and try again.",
            )
            status.configure(text="Connection failed.")
            save_button.configure(state="normal")
            return

        messagebox.showinfo(
            "MySQL configured",
            f"Connected to '{database or 'crime_radar'}'.\n\n"
            "The local .env file is saved. Restart the FastAPI server.",
        )
        root.destroy()

    buttons = ttk.Frame(root)
    buttons.grid(
        row=len(fields) + 2,
        column=0,
        columnspan=2,
        padx=14,
        pady=(4, 14),
        sticky="e",
    )
    ttk.Button(buttons, text="Cancel", command=root.destroy).pack(
        side="right", padx=(8, 0)
    )
    save_button = ttk.Button(buttons, text="Test and save", command=test_and_save)
    save_button.pack(side="right")
    inputs["Password"][1].focus_set()
    root.mainloop()
