# envr v0.5.7
# https://www.github.com/JPHutchins/envr
# https://www.crumpledpaper.tech

# MIT License
# Copyright (c) 2022-2024 JP Hutchins
# License text at the bottom of this source file

# Usage: . ./envr.ps1

# To "install" envr, add the alias `envr = . ./envr.ps1` to your shell profile.
# This will allow you to run `envr` instead of `. ./envr.ps1` to activate the 
# environment defined for the current directory,

# Windows (PowerShell) "installation":
#   Add-Content -Path $profile -Value "function envr { . ./envr.ps1 }"

# bash "installation":
#   echo "alias envr='. ./envr.ps1'" >> ~/.bashrc

# zsh "installation":
#   echo "alias envr='. ./envr.ps1'" >> ~/.zshrc

# The following line is for PowerShell/bash cross compatability.
# - The bash section shall begin with the delimiter "<#'"
# - The bash section shall end with the delimiter "#>"
echo --% > /dev/null ; : ' | out-null
<#'

# Check for bash or zsh then verify script is sourced
if [[ -n "${BASH:-}" ]] ; then
    if [[ "${BASH_SOURCE[0]}" = "${0}" ]] ; then
        ARG1=$1
        if [[ -z "$ARG1" || $ARG1 = "-h" || $ARG1 = "--help" ]] ; then
            echo -e "Usage: bash $> . envr.ps1"
            exit 1
        else
            echo "Unknown argument: $ARG1"
            exit 1
        fi
    fi
elif [[ -n "${ZSH_VERSION:-}" ]] ; then
    if [[ $ZSH_EVAL_CONTEXT = :file$ ]] ; then
        ARG1=$1
        if [[ -z "$ARG1" || $ARG1 = "-h" || $ARG1 = "--help" ]] ; then
            echo -e "Usage: zsh $> . envr.ps1"
            exit 1
        else
            echo "Unknown argument: $ARG1"
            exit 1
        fi
    fi
else 
    echo -e "\033[0;31mERROR - Script was not sourced from zsh or bash.\033[0m"
    echo -e "Usage: prompt $> . envr.ps1"
    return 1         
fi

KEY_NOT_FOUND_ERROR=255

zsh_emulate_ksh () {
    if [[ -n "$ZSH_VERSION" ]] ; then
        emulate -L ksh
    fi
}

_envr_check_for_config () {
    _ENVR_HAS_DEFAULT_CONFIG=0
    if [[ -f "envr-default" ]] ; then
        _ENVR_HAS_DEFAULT_CONFIG=1
    else 
        echo -e "\033[0;31mERROR: an envr-default configuration file must exist.\033[0m"
        unset _ENVR_HAS_DEFAULT_CONFIG
        return 1
    fi

    _ENVR_HAS_LOCAL_CONFIG=0
    if [[ -f "envr-local" ]] ; then
        _ENVR_HAS_LOCAL_CONFIG=1
    fi
}

unsource () {
    zsh_emulate_ksh

    # deactivate the python venv:
    if [[ -n "${BASH:-}" ]] ; then
        if [[ $(type -t deactivate) == function ]] ; then
            deactivate
        fi
    elif [[ -n "${ZSH_VERSION:-}" ]] ; then
        if [[ $(type deactivate) == function ]] ; then
            deactivate
        fi
    fi

    # reset to the old PATH:
    if [[ -n "${_ENVR_OLD_PATH:-}" ]] ; then
        PATH="${_ENVR_OLD_PATH:-}"
        export PATH
        unset _ENVR_OLD_PATH
    fi

    # reset to the old prompt:
    if [[ -n "${_ENVR_OLD_ENVIRONMENT_PS1:-}" ]] ; then
        PS1="${_ENVR_OLD_ENVIRONMENT_PS1:-}"
        export PS1
        unset _ENVR_OLD_ENVIRONMENT_PS1
    fi

    # Remove project options:
    for (( i = 0; i <= ${#_ENVR_PROJECT_OPTIONS[@]}; i++ )) ; do
        local KEY=$(echo ${_ENVR_PROJECT_OPTIONS[i]/%=*/})
        if [[ -n "${KEY:-}" ]] ; then
            unset $KEY
        fi
    done
    
    # Remove added environment variables:
    for (( i = 0; i <= ${#_ENVR_NEW_ENVIRONMENT_VARS[@]}; i++ )) ; do
        local KEY=$(echo ${_ENVR_NEW_ENVIRONMENT_VARS[i]/%=*/})
        if [[ -n "${KEY:-}" ]] ; then
            unset $KEY
        fi
    done
    # And restore any environment variables that were overwritten:
    for (( i = 0; i <= ${#_ENVR_OVERWRITTEN_ENVIRONMENT_VARS[@]}; i++ )) ; do
        local ENVAR=${_ENVR_OVERWRITTEN_ENVIRONMENT_VARS[i]}
        if [[ -n "${ENVAR:-}" ]] ; then
            export "$ENVAR"
        fi
    done

    # Remove added aliases:
    for env_var in "${_ENVR_NEW_ALIASES[@]}"; do
        KEY=$(echo ${env_var/%=*/})
        unalias "$KEY" 2>/dev/null  # entry may appear twice, silence error
    done
    # And restore any aliases that were overwritten:
    for alias_entry in "${_ENVR_OVERWRITTEN_ALIASES[@]}"; do
        alias "$alias_entry"
    done

    # This should detect bash and zsh, which have a hash command that must
    # be called to get it to forget past commands.  Without forgetting
    # past commands the $PATH changes we made may not be respected
    if [ -n "${BASH:-}" -o -n "${ZSH_VERSION:-}" ] ; then
        hash -r
    fi

    if [[ ! "${1:-}" = "nondestructive" ]] ; then
    # Self destruct!
        unset _ENVR_HAS_DEFAULT_CONFIG
        unset _ENVR_HAS_LOCAL_CONFIG
    fi

    unset _ENVR_PROJECT_OPTIONS
    unset _ENVR_NEW_ENVIRONMENT_VARS
    unset _ENVR_OVERWRITTEN_ENVIRONMENT_VARS
    unset _ENVR_NEW_ALIASES
    unset _ENVR_OVERWRITTEN_ALIASES
    unset _ENVR_NEW_PATH
    unset _ENVR_PATH_ADDITIONS
    unset VIRTUAL_ENV_DISABLE_PROMPT
    unset ENVR_ROOT
    unset ENVR_PROJECT_NAME
}

_envr_init_private_variables () {
    _ENVR_PROJECT_OPTIONS=()
    _ENVR_NEW_ENVIRONMENT_VARS=()
    _ENVR_OVERWRITTEN_ENVIRONMENT_VARS=()
    _ENVR_NEW_ALIASES=()
    _ENVR_OVERWRITTEN_ALIASES=()
    _ENVR_PATH_ADDITIONS=()
    _ENVR_NEW_PATH="$PATH"
}

_envr_get_index () {
    local key=$1
    shift
    local list=("$@")

    local list_length="${#list[@]}"
    if [[ $list_length -gt 255 ]] ; then
        exit 2  # can only return 0 - 255; not supporing more than 256 entries
    fi

    local index=$KEY_NOT_FOUND_ERROR
    for (( i = 0; i <= $list_length; i++ )) ; do
        local KEY=$(echo ${list[i]/%=*/})
        if [[ "$key" = "$KEY" ]] ; then
            index=$i
            break
        fi
    done
    echo $index
}

_envr_parse_config () {
    ENVR_ROOT=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
    export ENVR_ROOT="$ENVR_ROOT"

    local config_file=$1
    local envr_config_category="INITIAL"
    local config_file_line_number=0

    while IFS= read -r line <&3 || [[ -n "$line" ]] ; do
        config_file_line_number=$((config_file_line_number + 1))

        # trim whitespace and continue if line is blank
        local line="$(echo $line | xargs)"
        if [[ "$line" = "" ]] ; then
            continue
        fi

        # ignore comments
        if [[ "#" = $(echo ${line:0:1}) ]] ; then
            continue
        fi

        # get key value of entry, if any, e.g. KEY=VALUE
        local KEY=$(echo ${line/%=*/})
        local VALUE=$(echo ${line#${KEY}=})

        # check for update to envr_config_category, choosing what is set
        if [[ "[" = $(echo ${line:0:1}) ]] ; then
            envr_config_category="$line"

        # update the list of project options
        elif [[ "$envr_config_category" = "[PROJECT_OPTIONS]" ]] ; then
            local index=$(_envr_get_index "$KEY" "${_ENVR_PROJECT_OPTIONS[@]}")
            if [[ $index == $KEY_NOT_FOUND_ERROR ]] ; then
                _ENVR_PROJECT_OPTIONS+=( "$KEY=$VALUE" )
            else
                _ENVR_PROJECT_OPTIONS[$index]="$KEY=$VALUE"
            fi

        # update the list of new environment variables
        elif [[ "$envr_config_category" = "[VARIABLES]" ]] ; then
            local index=$(_envr_get_index "$KEY" "${_ENVR_NEW_ENVIRONMENT_VARS[@]}")
            if [[ $index == $KEY_NOT_FOUND_ERROR ]] ; then
                _ENVR_NEW_ENVIRONMENT_VARS+=( "$KEY=$VALUE" )
            else
                _ENVR_NEW_ENVIRONMENT_VARS[$index]="$KEY=$VALUE"
            fi
        
        # update the list of new aliases
        elif [[ "$envr_config_category" = "[ALIASES]" ]] ; then
            local index=$(_envr_get_index "$KEY" "${_ENVR_NEW_ALIASES[@]}")
            if [[ $index == $KEY_NOT_FOUND_ERROR ]] ; then
                _ENVR_NEW_ALIASES+=( "$KEY=$VALUE" )
            else
                _ENVR_NEW_ALIASES[$index]="$KEY=$VALUE"
            fi 

        # update the list of additions to system PATH
        elif [[ "$envr_config_category" = "[ADD_TO_PATH]" ]] ; then
            local index=$(_envr_get_index "$KEY" "${_ENVR_PATH_ADDITIONS[@]}")
            if [[ $index == $KEY_NOT_FOUND_ERROR ]] ; then
                _ENVR_PATH_ADDITIONS+=( "$KEY=$VALUE" )
            else
                _ENVR_PATH_ADDITIONS[$index]="$KEY=$VALUE"
            fi 

        # parsing error
        else
            echo -e "\033[0;31mERROR\033[0m - line $config_file_line_number of ${config_file}: $line under section $envr_config_category unknown."
            return 1
        fi
    done 3< "$1"
}

_envr_set_prompt_prefix () {
    if [[ -z "${ENVIRONMENT_DISABLE_PROMPT:-}" ]] ; then
        _ENVR_OLD_ENVIRONMENT_PS1="${PS1:-}"
        if [[ -n "${_ENVR_PROJECT_NAME:-}" ]] ; then
            _PROMPT="$_ENVR_PROJECT_NAME"
        else
            _PROMPT="envr"
        fi

        if [[ -n "${BASH:-}" ]] ; then
            PS1="\[\033[0;36m\](${_PROMPT}) ${PS1:-}"
        elif [[ -n "${ZSH_VERSION:-}" ]] ; then
            PS1="%F{36}(${_PROMPT})%F{reset} ${PS1:-}"
        fi
        
        export PS1
    fi
}

_envr_forget_hash () {
    # This should detect bash and zsh, which have a hash command that must
    # be called to get it to forget past commands.  Without forgetting
    # past commands the $PATH changes we made may not be respected
    if [ -n "${BASH:-}" -o -n "${ZSH_VERSION:-}" ] ; then
        hash -r
    fi
}

_envr_activate_python_venv () {
    # Activate the python venv if specified
    if [[ -n "${_ENVR_PYTHON_VENV:-}" ]] ; then
        if [[ -z "${ENVIRONMENT_DISABLE_PROMPT:-}" ]] ; then
            # We're using the envr prompt; disable the python (venv) prompt
            VIRTUAL_ENV_DISABLE_PROMPT="true"
        fi
        source "${_ENVR_PYTHON_VENV}/bin/activate"
    fi
}

_envr_main () {
    _envr_check_for_config &&
    unsource nondestructive &&
    _envr_init_private_variables &&

    # Always parse the default config first
    _envr_parse_config "envr-default"

    if [[ $? == 1 ]] ; then
        unsource
        return 1
    fi
    
    # Parse the local config
    if [[ $_ENVR_HAS_LOCAL_CONFIG = 1 ]] ; then
        _envr_parse_config "envr-local" 
    fi

    if [[ $? == 1 ]] ; then
        unsource
        return 1
    fi

    # Apply the project options
    for option in "${_ENVR_PROJECT_OPTIONS[@]}"; do
        local key=$(echo ${option/%=*/})
        local value=$(echo ${option#${key}=})

        case "$key" in
            "PROJECT_NAME")
                _ENVR_PROJECT_NAME="$value"
                export ENVR_PROJECT_NAME=$_ENVR_PROJECT_NAME;;
            "PYTHON_VENV")
                _ENVR_PYTHON_VENV="$value";;
            *)
                echo -e "\033[0;31mERROR - line $config_file_line_number of ${config_file}: $line under section $envr_config_category unknown.\033[0m"
                unsource
                return 1;;
        esac
    done

    # Apply the environment changes
    for env_var in "${_ENVR_NEW_ENVIRONMENT_VARS[@]}"; do
        local key=$(echo ${env_var/%=*/})

        # check if we are overwriting an existing environment variable
        if [[ -n "${BASH:-}" ]] ; then
            local old_value=$(printf '%s\n' "${!key}")
        elif [[ -n "${ZSH_VERSION:-}" ]] ; then
            local old_value=$(printf '%s\n' "${(P)key}")
        fi
        if [[ -n "$old_value" ]] ; then
            _ENVR_OVERWRITTEN_ENVIRONMENT_VARS+=( "${key}=${old_value}" )
        fi

        # expand the variables
        if [[ -n "${BASH:-}" ]] ; then
            if [[ $(printf %.1s $BASH_VERSION) -ge 5 ]] ; then
                env_var="${env_var@P}"
            else  # bash < 4.4 doesn't have @P
                env_var="$(eval echo "$env_var")"
            fi
        elif [[ -n "${ZSH_VERSION:-}" ]] ; then
            env_var="${(e)env_var}"
        fi
        
        export "$env_var"
    done

    # Apply the the new aliases and save overwrites
    for alias in "${_ENVR_NEW_ALIASES[@]}"; do
        local key=$(echo ${alias/%=*/})
        local value=$(echo ${alias#${key}=})

        # check if we are overwriting an alias
        if [[ -n "${BASH:-}" ]] ; then
            if [[ "$(type -t ${key})" = "alias" ]] ; then
                local alias_output=$(alias ${key})
                local old_value=$(echo ${alias_output#alias })
                _ENVR_OVERWRITTEN_ALIASES+=("$old_value")
            fi 
        elif [[ -n "${ZSH_VERSION:-}" ]] ; then 
            if [[ ${+aliases[${key}]} ]] ; then
                local old_value=$(alias ${key})
                _ENVR_OVERWRITTEN_ALIASES+=("$old_value")
            fi 
        fi
        alias "$key=$value"
    done

    # Apply additions to the system PATH
    for path_dir in "${_ENVR_PATH_ADDITIONS[@]}"; do
        local key=$(echo ${path_dir/%=*/})
        local value=$(echo ${path_dir#${key}=})

        local dir=""
        # expand the variables
        if [[ -n "${BASH:-}" ]] ; then
            if [[ $(printf %.1s $BASH_VERSION) -ge 5 ]] ; then
                dir="${value@P}"
            else  # bash < 4.4 doesn't have @P
                dir="$(eval echo "$value")"
            fi
        elif [[ -n "${ZSH_VERSION:-}" ]] ; then
            dir="${(e)value}"
        fi

        # warn if the directory does not exist
        if [[ ! -d "$dir" ]] ; then
            echo -e "\033[0;33mWARNING\033[0m - ${key}=$dir is not a directory."
        fi
        # don't add duplicate directories to PATH
        if [[ ":${_ENVR_NEW_PATH}:" == *":${dir}:"* ]]; then
            continue
        fi
        _ENVR_NEW_PATH="${dir}:${_ENVR_NEW_PATH}"
    
    done

    # Save the unmodified PATH and export the new one
    _ENVR_OLD_PATH="$PATH"
    PATH="$_ENVR_NEW_PATH"
    export PATH

    _envr_set_prompt_prefix &&
    _envr_forget_hash &&
    _envr_activate_python_venv
    return 0
}

# run main
_envr_main

true << 'POWERSHELL_SECTION'
#>

# parse the environment file and setup
$global:_ENVR_PROJECT_OPTIONS = @{}
$global:_ENVR_NEW_ENVIRONMENT_VARS = [ordered]@{}
$global:_ENVR_OVERWRITTEN_ENVIRONMENT_VARS = @{}
$global:_ENVR_NEW_ALIASES = [ordered]@{}
$global:_ENVR_OVERWRITTEN_ALIASES = @{}
$global:_ENVR_PATH_ADDITIONS = @{}

# setup helpers for alias hack
$global:_ALIAS_FN_INDEX = 0
$global:_ALIAS_COMMAND_ARR = @()
$global:_ALIAS_ARGS_ARR = @()

$global:_ENVR_PROJECT_NAME = $null
$global:_ENVR_PYTHON_VENV = $null

function global:unsource ([switch]$NonDestructive) {
    # Revert to original values

    # Deactivate the python venv:
    if (Test-Path -Path Function:deactivate) {
        deactivate
    }

    # Reset to the old PATH:
    if (Test-Path -Path Env:_OLD_PATH) {
        Copy-Item -Path Env:_OLD_PATH -Destination Env:PATH
        Remove-Item -Path Env:_OLD_PATH
    }

    # The prior prompt:
    if (Test-Path -Path Function:_OLD_VIRTUAL_PROMPT) {
        Copy-Item -Path Function:_OLD_VIRTUAL_PROMPT -Destination Function:prompt
        Remove-Item -Path Function:_OLD_VIRTUAL_PROMPT
    }

    # Just remove the _ENVAR_PROMPT_PREFIX altogether:
    if (Get-Variable -Name "_ENVAR_PROMPT_PREFIX" -ErrorAction SilentlyContinue) {
        Remove-Variable -Name _ENVAR_PROMPT_PREFIX -Scope Global -Force
    }

    # Remove added environment variables:
    foreach ($key in $global:_ENVR_NEW_ENVIRONMENT_VARS.Keys) {
        if (Test-Path -Path env:$key) {
            Remove-Item -Path env:$key
        }
    }
    # And restore any environment variables that were overwritten:
    $global:_ENVR_OVERWRITTEN_ENVIRONMENT_VARS.GetEnumerator().ForEach({
        $key = $($_.Key)
        $val = $($_.Value)

        if (Test-Path -Path env:$key) {
            Write-Host "ERROR: $key should have been removed already!"
            return 1
        }
        Set-Item -Path env:$key -Value $val
    })

    # Remove added aliases:
    foreach ($key in $global:_ENVR_NEW_ALIASES.Keys) {
        if (Test-Path -Path alias:$key) {
            Remove-Item alias:$key
        }
        else {
            Write-Host "WARNING: alias $key was removed already!"
        }
    }
    # And restore any aliases that were overwritten:
    $global:_ENVR_OVERWRITTEN_ALIASES.GetEnumerator().ForEach({
        $key = $($_.Key)
        $val = $($_.Value)

        Set-Alias $key $val -Scope Global -Option AllScope -Force
    })

    # Leave unsource function in the global namespace if requested:
    if (-not $NonDestructive) {
        Remove-Item -Path function:unsource
    }

    if (Get-Variable -Name "ENVR_ROOT" -ErrorAction SilentlyContinue) {
        Remove-Variable -Name ENVR_ROOT -Scope Global -Force
    }

    # Remove project options:
    if (Test-Path -Path env:ENVR_ROOT) {
        Remove-Item -Path env:ENVR_ROOT
    }
    if (Test-Path -Path env:ENVR_PROJECT_NAME) {
        Remove-Item -Path env:ENVR_PROJECT_NAME
    }

    # Remove variables leftover from script run
    # $_VAR_REMOVE_LIST = 
    #     "_PROJECT_NAME",
    #     "_PYTHON_VENV_SCRIPT",
    #     "_NEW_ENVIRONMENT_VARS",
    #     "_OVERWRITTEN_ENVIRONMENT_VARS",
    #     "_NEW_ALIASES",
    #     "_CATEGORY",
    #     "_PYTHON_VENV",
    #     "_TEMP_ARRAY"
    # foreach ($var in $_VAR_REMOVE_LIST) {
    #     $p = Get-Variable -Name $var -ErrorAction SilentlyContinue
    #     echo $p
    #     if (Get-Variable -Name $var -ErrorAction SilentlyContinue) {
    #         Remove-Variable -Name $var -Scope Global -Force
    #     }
    # }

    if (Test-Path -Path env:VIRTUAL_ENV_DISABLE_PROMPT) {
        Remove-Item -Path env:VIRTUAL_ENV_DISABLE_PROMPT
    }
}

function global:parse($config) {
    $category = "INITIAL"
    foreach ($line in Get-Content $config) {
        # trim whitespace and continue if line is blank 
        $line = $line.Trim()
        if ($line -eq "") {
            continue
        }

        # ignore comments
        if ($line.SubString(0,1) -eq "#") {
            continue
        }

        # get key value of entry, if any, e.g. KEY=VALUE
        $_TEMP_ARRAY = $line.split("=", 2)
        $KEY = $_TEMP_ARRAY[0]
        $VALUE = $_TEMP_ARRAY[1]

        # check for update to _CATEGORY, choosing what is set
        if ($line.SubString(0,1) -eq "[") {
            $category = $line
        }

        # update the hashtable of project options
        elseif ($category -eq "[PROJECT_OPTIONS]") {
            $global:_ENVR_PROJECT_OPTIONS[$KEY] = $VALUE
        }
        # update the hashtable of environment variables
        elseif ($category -eq "[VARIABLES]") {
            $global:_ENVR_NEW_ENVIRONMENT_VARS[$KEY] = $VALUE
        }
        # update the hashtable of new aliases
        elseif ($category -eq "[ALIASES]") {
            $global:_ENVR_NEW_ALIASES[$KEY] = $VALUE
        }
        # update the hashtable of additions to system PATH
        elseif ($category -eq "[ADD_TO_PATH]") {
            $global:_ENVR_PATH_ADDITIONS[$KEY] = $VALUE
        }
    }
}

# Deactivate any currently active virtual environment, but leave the
# deactivate function in place.
unsource -nondestructive

# Save the old path
Copy-Item -Path Env:PATH -Destination Env:_OLD_PATH

New-Variable -Name ENVR_ROOT -Description "envr parent folder path" -Scope Global -Option ReadOnly -Visibility Public -Value "$PSScriptRoot"
$global:_ENVR_NEW_ENVIRONMENT_VARS["ENVR_ROOT"] = $ENVR_ROOT

# Always parse the default config first
if (Test-Path -Path envr-default) {
    parse envr-default
} else {
    Write-Host "ERROR: an envr-default configuration file must exist." -ForegroundColor Red
    unsource
    return
}    

# Check for local config
if (Test-Path -Path envr-local) {
    parse envr-local
}

# Apply the project options
$global:_ENVR_PROJECT_OPTIONS.GetEnumerator().ForEach({
    $key = $($_.Key)
    $val = $($_.Value)

    switch ($key)
    {
        "PROJECT_NAME" { 
            $global:_ENVR_PROJECT_NAME = $val
            Set-Item -Path env:ENVR_PROJECT_NAME -Value $val
            $global:_ENVR_NEW_ENVIRONMENT_VARS["ENVR_PROJECT_NAME"] = $val
        }
        "PYTHON_VENV" { $global:_ENVR_PYTHON_VENV = $val }
    }
    
})

# Apply the environment changes
$global:_ENVR_NEW_ENVIRONMENT_VARS.GetEnumerator().ForEach({
    $key = $($_.Key)
    $val = $($_.Value)

    # check if we are overwriting an environment variable
    if (Test-Path -Path env:$key) {
        $original_val = [System.Environment]::GetEnvironmentVariable($key)
        if ($global:_ENVR_OVERWRITTEN_ENVIRONMENT_VARS.ContainsKey($key)) {
            Write-Host "Error: variable $key is already overwritten"
            unsource
            exit 1
        }
        $global:_ENVR_OVERWRITTEN_ENVIRONMENT_VARS[$key] = $original_val
    }

    # expand the variables
    if ($null -ne $val) {
        $val = $ExecutionContext.InvokeCommand.ExpandString($val.Replace('$', '$env:'))
    }

    Set-Item -Path env:$key -Value $val
})

# Apply the new aliases and save overwrites
$global:_ENVR_NEW_ALIASES.GetEnumerator().ForEach({
    $key = $($_.Key)
    $val = $($_.Value)

    # check if we are overwriting an alias
    if (Test-Path -Path alias:$key) {
        $_OLD_ALIAS = $((Get-Alias $key).Definition)
        $global:_ENVR_OVERWRITTEN_ALIASES[$key] =$_OLD_ALIAS
        Remove-Item -Path Alias:$key
    }
    if ($global:_ALIAS_FN_INDEX -eq 10) {
        Write-Host "ERROR: only $global:_ALIAS_FN_INDEX aliases allowed!"
        return 1
    }

    $global:_ALIAS_COMMAND_ARR += ,$ExecutionContext.InvokeCommand.ExpandString($val)

    # Hack to support aliases with parameters
    function script:_ENVR_ALIAS_FN_0 { Invoke-Expression "$($global:_ALIAS_COMMAND_ARR[0]) $args" }
    function script:_ENVR_ALIAS_FN_1 { Invoke-Expression "$($global:_ALIAS_COMMAND_ARR[1]) $args" }
    function script:_ENVR_ALIAS_FN_2 { Invoke-Expression "$($global:_ALIAS_COMMAND_ARR[2]) $args" }
    function script:_ENVR_ALIAS_FN_3 { Invoke-Expression "$($global:_ALIAS_COMMAND_ARR[3]) $args" }
    function script:_ENVR_ALIAS_FN_4 { Invoke-Expression "$($global:_ALIAS_COMMAND_ARR[4]) $args" }
    function script:_ENVR_ALIAS_FN_5 { Invoke-Expression "$($global:_ALIAS_COMMAND_ARR[5]) $args" }
    function script:_ENVR_ALIAS_FN_6 { Invoke-Expression "$($global:_ALIAS_COMMAND_ARR[6]) $args" }
    function script:_ENVR_ALIAS_FN_7 { Invoke-Expression "$($global:_ALIAS_COMMAND_ARR[7]) $args" }
    function script:_ENVR_ALIAS_FN_8 { Invoke-Expression "$($global:_ALIAS_COMMAND_ARR[8]) $args" }
    function script:_ENVR_ALIAS_FN_9 { Invoke-Expression "$($global:_ALIAS_COMMAND_ARR[9]) $args" }
    Set-Alias -Name $key -Value "_ENVR_ALIAS_FN_$global:_ALIAS_FN_INDEX" -Scope script
    $global:_ALIAS_FN_INDEX += 1
})

# Apply the additions to the system PATH
$global:_ENVR_PATH_ADDITIONS.GetEnumerator().ForEach({
    $key = $($_.Key)
    $val = $($_.Value)

    # expand the variables
    if ($null -ne $val) {
        $val = $ExecutionContext.InvokeCommand.ExpandString($val.Replace('$', '$env:'))
    }

    if (Test-Path -Path "$val") {
    } else {
        Write-Host "WARNING - $key=$val is not a directory." -ForegroundColor Yellow
    }
    foreach ($folder in $(Get-Item env:path).value.split($([System.IO.Path]::PathSeparator))) {
        if ($folder -eq $val) {
            $duplicate = 1
        }
    }
    if ($duplicate -eq 1) {
        continue
    }

    $Env:PATH = "$val$([System.IO.Path]::PathSeparator)$Env:PATH"
})

# Activate the python venv if specified
if (-not $global:_ENVR_PYTHON_VENV -eq "") {
    if (-not $Env:ENVIRONMENT_DISABLE_PROMPT) {
        # We're going to set envr prompt; disable the python (venv) prompt
        Set-Item -Path env:VIRTUAL_ENV_DISABLE_PROMPT -Value "true"
    }
    . "$global:_ENVR_PYTHON_VENV/Scripts/Activate.ps1"
}

# Set the prompt prefix
if (-not $Env:ENVIRONMENT_DISABLE_PROMPT) {

    # Set the prompt to include the env name
    # Make sure _OLD_VIRTUAL_PROMPT is global
    function global:_OLD_VIRTUAL_PROMPT { "" }
    Copy-Item -Path function:prompt -Destination function:_OLD_VIRTUAL_PROMPT

    $prompt = "(envr) "
    if (-not $global:_ENVR_PROJECT_NAME -eq "") {
        $prompt = "($global:_ENVR_PROJECT_NAME) " 
    }
    New-Variable -Name _ENVAR_PROMPT_PREFIX -Description "Python virtual environment prompt prefix" -Scope Global -Option ReadOnly -Visibility Public -Value $prompt

    function global:prompt {
        Write-Host -NoNewline -ForegroundColor Cyan "$_ENVAR_PROMPT_PREFIX"
        _OLD_VIRTUAL_PROMPT
    }
}

# These lines deal with either script ending
echo --% > /dev/null ; : ' | out-null
<#'
POWERSHELL_SECTION
#>

# License text continued

# MIT License
# Copyright (c) 2022-2024 JP Hutchins

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
