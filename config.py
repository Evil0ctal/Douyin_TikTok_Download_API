import yaml

config_path = 'config.yml'

with open(config_path, 'r', encoding='utf-8') as file:
    config = yaml.safe_load(file)


def api_config():
    api_default_port = config['Web_API']['Port']
    api_new_port = input(
        f'Default API port: {api_default_port}\nIf you want use different port input new API port here: ')

    if api_new_port.isdigit():
        if int(api_new_port) == int(api_default_port):
            print(f'Use default port for web_app.py: {api_default_port}')
        else:
            print(f'Use new port for web_api.py: {api_new_port}')
            config['Web_API']['Port'] = int(api_new_port)
            with open(config_path, "w", encoding="utf-8") as file:
                yaml.dump(config, file, allow_unicode=True)
    else:
        print(f'Use default port for web_app.py: {api_default_port}')

    req_limit = config['Web_API']['Rate_Limit']
    new_req_limit = input(
        f'Default API rate limit: {req_limit}\nIf you want use different rate limit input new rate limit here: ')

    if new_req_limit.isdigit():
        if int(new_req_limit) == int(req_limit.split('/')[0]):
            print(f'Use default rate limit for web_api.py : {req_limit}')
        else:
            print(f'Use new rate limit: {new_req_limit}/minute')
            config['Web_API']['Rate_Limit'] = f'{new_req_limit}/minute'
            with open(config_path, "w", encoding="utf-8") as file:
                yaml.dump(config, file, allow_unicode=True)
    else:
        print(f'Use default rate limit for web_api.py: {req_limit}')


def app_config():
    app_default_port = config['Web_APP']['Port']
    app_new_port = input(
        f'Default App port: {app_default_port}\nIf you want use different port input new App port here: ')

    if app_new_port.isdigit():
        if int(app_new_port) == int(app_default_port):
            print(f'Use default port for web_app.py: {app_default_port}')
        else:
            print(f'Use new port: {app_new_port}')
            config['Web_APP']['Port'] = int(app_new_port)
            with open(config_path, "w", encoding="utf-8") as file:
                yaml.dump(config, file, allow_unicode=True)
    else:
        print(f'Use default port for web_app.py : {app_default_port}')


if __name__ == '__main__':
    api_config()
    app_config()
