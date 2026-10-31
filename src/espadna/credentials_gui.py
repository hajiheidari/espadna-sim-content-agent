"""Local masked entry only. No network, logs, clipboard reads or bot launch."""
import argparse
from .credentials import save_token
from .packs import private_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--path', required=True)
    args = parser.parse_args()
    path = private_path(args.path)
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.title('Espadna Sim — Private token setup')
    root.geometry('650x260')
    root.resizable(False, False)
    root.attributes('-topmost', True)
    font = ('Segoe UI', 11)
    tk.Label(root, text='ثبت خصوصی توکن ربات — Bot token', font=('Segoe UI', 14, 'bold')).pack(pady=(22, 10))
    tk.Label(root, text='توکن فقط روی همین رایانه با حساب ویندوز رمزگذاری می‌شود.\nذخیرهٔ توکن هیچ پیامی ارسال نمی‌کند. آن را در چت نفرستید.', font=font, justify='center').pack()
    entry = tk.Entry(root, show='●', font=font, width=62)
    entry.pack(pady=18)
    entry.focus_set()
    def save(event=None):
        try:
            save_token(path, entry.get())
        except (ValueError, OSError):
            messagebox.showerror('ثبت انجام نشد', 'فرمت توکن یا محل ذخیره معتبر نیست. توکن BotFather را کامل وارد کنید.', parent=root)
            return
        entry.delete(0, tk.END)
        messagebox.showinfo('ثبت شد', 'توکن به‌صورت رمزگذاری‌شده ذخیره شد.\nربات هنوز اجرا نشده و پیامی ارسال نشده است.', parent=root)
        root.destroy()
    tk.Button(root, text='ذخیرهٔ خصوصی / Save', command=save, font=font, width=26).pack()
    root.bind('<Return>', save)
    root.mainloop()
    print('Private token setup closed; no credential values are logged.')


if __name__ == '__main__':
    main()
