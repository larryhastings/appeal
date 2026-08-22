import appeal
app = appeal.Appeal()
@app.command()
def boom():
    import sys
    sys.exit("this string must reach stderr")
app.main()
